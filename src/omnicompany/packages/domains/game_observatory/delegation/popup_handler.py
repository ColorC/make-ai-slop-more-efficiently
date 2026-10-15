# [OMNI] origin=codex created_by=codex intent="restrict Romantic Restaurant popup handling to surveyed safe actions"
"""Strict popup-action whitelist for Romantic Restaurant navigation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .contracts import ActionRequestV1
from .navigation import ScreenIdV1, SurfaceRegistry, frame_to_rgb


PopupActionKind = Literal[
    "close",
    "free-claim",
    "tutorial-follow",
    # F4-11, planner ruling of 2026-09-07.  Advancing a story dialogue is
    # legitimate play, not a guide follow, and it needs its own name so the
    # story-advance fact has one source: the guard records this kind and the
    # main-task flow records `main-task:story:{n}`, and a reader that counts
    # story progress reads both rather than double-counting one.
    "story-advance",
    "selection",
    "confirm",
    "wait",
]


# F4-12 Phase 0, canonical ruling 1.  The story-dialogue advance exists exactly
# once, in ``story_advance_action`` below; the guard, the main-task flow and
# the loop's round-level continuation all go through it.  Only the budgets
# differ by context, and they live here too: the unattended guard spends at
# most twenty advances per dialogue (the 2026-09-07 ruling, counted across
# rounds), the main-task flow at most forty per session.
STORY_DIALOGUE_SURFACE = "story-dialogue"
STORY_ADVANCE_GUARD_BUDGET = 20
STORY_ADVANCE_FLOW_BUDGET = 40


class PopupActionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    screen: str
    kind: PopupActionKind
    element: str | None
    request: ActionRequestV1
    safety_basis: str


def _tap(
    registry: SurfaceRegistry,
    screen: str,
    element: str,
    kind: PopupActionKind,
    *,
    settle: Literal["fixed", "stable"] = "stable",
) -> PopupActionV1:
    metadata = registry.element(screen, element)
    if metadata.get("forbidden") or metadata.get("allowed") is False:
        raise ValueError("forbidden registry element cannot become a popup action")
    x, y = registry.point(screen, element)
    return PopupActionV1(
        screen=screen,
        kind=kind,
        element=element,
        request=ActionRequestV1(
            kind="tap",
            intent=f"popup:{kind}:{screen}:{element}",
            params={"x": x, "y": y},
            settle=settle,
            # A static old popup is not evidence that a navigation tap has
            # completed. Nested item dialogs share one entity, so their
            # surveyed per-variant return rules may supply an exact target.
            expected_screen=metadata.get("expected_screen"),
            excluded_screen=(
                screen
                if kind in {"close", "free-claim"}
                and not metadata.get("expected_screen")
                and not registry.surfaces.get(screen, {}).get("nested_dialog")
                else None
            ),
        ),
        safety_basis="exact coordinate is present in the surveyed navigation whitelist",
    )


def story_advance_action(
    screen: ScreenIdV1,
    registry: SurfaceRegistry,
) -> PopupActionV1:
    """The one story-dialogue advance, shared by the guard and the flow.

    F4-11, planner ruling of 2026-09-07; F4-12 Phase 0 ruling 1 made it the
    single implementation.  The final continue button is preferred whenever
    its anchor matched -- it ends the dialogue and returns to the restaurant
    scene, which has a verified route back to the board -- and the open area
    advances the dialogue otherwise.
    """

    final_anchor = "story-dialogue/elements/final-continue-button"
    element = (
        "final-continue-button"
        if final_anchor in (screen.matched_anchors or ())
        else "advance-open-area"
    )
    return _tap(registry, screen.name, element, "story-advance")


def action_for_screen(
    screen: ScreenIdV1,
    registry: SurfaceRegistry,
    *,
    attempt: int = 0,
    rave_reviews_reward_earned: bool = False,
) -> PopupActionV1 | None:
    """Return only an explicitly registered close, free, guide, or wait action."""

    if screen.name == "transition":
        return PopupActionV1(
            screen="transition",
            kind="wait",
            element=None,
            request=ActionRequestV1(
                kind="wait",
                intent="popup:wait:registered-transition",
                params={"ms": 200},
                capture_pre=False,
                capture_post=False,
            ),
            safety_basis="transition frames permit waiting but no coordinate action",
        )
    if screen.name == "story-cutscene":
        if attempt == 0:
            return PopupActionV1(
                screen=screen.name,
                kind="wait",
                element=None,
                request=ActionRequestV1(
                    kind="wait",
                    intent="popup:wait:story-cutscene:observe-three-seconds",
                    params={"ms": 3000},
                    capture_pre=False,
                    capture_post=False,
                ),
                safety_basis="registered cutscene is observed for three seconds first",
            )
        if attempt in {1, 2}:
            # F4-06: the 2_4 chalkboard cutscene has a two-stage skip control.
            # The first tap only expands the chevron into its labelled pill; the
            # second is what actually skips.  Both taps land on the same
            # registered coordinate, and the surface exposes no other control.
            return _tap(
                registry,
                screen.name,
                "skip-once",
                "tutorial-follow",
                settle="fixed",
            )
        return None
    forbidden_close = (
        registry.forbidden_close_element(screen.name)
        if registry.is_forbidden_surface(screen.name)
        else None
    )
    if forbidden_close is not None:
        # A registered red-line surface exposes its close control and nothing
        # else, whatever the rest of this whitelist would otherwise offer.
        return _tap(registry, screen.name, forbidden_close, "close")
    recovery = registry.surfaces.get(screen.name, {}).get("recovery_action", {})
    if recovery.get("kind") == "close":
        element = recovery.get("element", "close")
        for variant, specification in registry.surfaces[screen.name].get("recognition_variants", {}).items():
            if f"{screen.name}/recognition_variants/{variant}" in screen.matched_anchors:
                element = specification.get("close_element", element)
                break
        return _tap(registry, screen.name, element, "close")
    if screen.name == "warehouse-price-panel":
        return _tap(registry, screen.name, "close", "close")
    if screen.name == STORY_DIALOGUE_SURFACE:
        # F4-11, planner ruling of 2026-09-07.  The 07:56Z stop sat on a
        # registered surface with verified elements and no action wired to it,
        # so the run could not leave a dialogue it had walked into itself.
        # The budgets live in the callers: STORY_ADVANCE_GUARD_BUDGET per
        # dialogue on the guard path, STORY_ADVANCE_FLOW_BUDGET per session in
        # the main-task flow, and three unchanged frames stop the follow.
        return story_advance_action(screen, registry)
    if screen.name == "board-story-prompt":
        return _tap(
            registry,
            screen.name,
            "advance-bubble",
            "tutorial-follow",
        )
    if screen.name == "rave-reviews-event-intro":
        return _tap(registry, screen.name, "close-only", "close")
    if screen.name == "energy-party-announcement":
        # F4-10, planner ruling of 2026-09-07.  An announcement, not a grant:
        # it says that finishing orders now pays extra energy.  Nothing on it
        # carries a price, a gem cost, an advertisement, a login or an external
        # link, and the ruling allowed either its acknowledge button or its X.
        # The X is the narrower control, so it is the one wired up.
        return _tap(registry, screen.name, "close-only", "close")
    if screen.name == "energy-party-announcement-fullscreen":
        # 2026-09-08.  The same 能量狂欢！ announcement as a fullscreen overlay
        # over the dimmed board: a ticking countdown, no X, no button, just
        # "tap anywhere to continue".  Nothing on it carries a price, a gem
        # cost, an advertisement, a login or an external link, so one tap on
        # the registered safe point dismisses it, like the panel variant's X.
        return _tap(registry, screen.name, "tap-anywhere-continue", "close")
    if screen.name == "rave-reviews-complete-panel":
        # F4-09 left this close-only: a one-time bounded claim is not a standing
        # licence to claim every time the surface appears.  2026-09-16, user
        # ruling: when 好评订单 has actually reached its cap the reward is earned
        # and must be taken, so the caller passes that authority in from the
        # authoritative save (curScore >= MaxScore) rather than the guard
        # assuming it.  Without that evidence the panel is still close-only.
        if rave_reviews_reward_earned:
            return _tap(registry, screen.name, "claim", "free-claim")
        return _tap(registry, screen.name, "close-only", "close")
    if screen.name == "level-up-reward":
        return _tap(
            registry,
            screen.name,
            "claim-free-level-reward",
            "free-claim",
        )
    if screen.name == "intro-chest-real-money-offer":
        return _tap(registry, screen.name, "close-only", "close")
    if screen.name == "forced-shop-tutorial":
        return _tap(
            registry,
            screen.name,
            "guided-shop-cart",
            "tutorial-follow",
        )
    if screen.name == "tutorial-overlay":
        return _tap(
            registry,
            screen.name,
            "guided-target",
            "tutorial-follow",
        )
    if screen.name == "decoration-choice":
        return _tap(registry, screen.name, "left-option", "selection")
    if screen.name == "decoration-confirmation":
        return _tap(registry, screen.name, "confirm", "confirm")
    if screen.name == "newbie-gift":
        return _tap(
            registry,
            screen.name,
            "open-gift",
            "free-claim",
            settle="fixed",
        )
    if screen.name == "daily-rewards":
        # 2026-09-09, level-7 check-in.  The claim is one tap on the
        # claimable day cell (free daily grant); the close is the durable
        # departure once the cell stops being the claimable one.
        if attempt == 0:
            return _tap(
                registry,
                screen.name,
                "claim-daily-reward",
                "free-claim",
                settle="fixed",
            )
        if attempt == 1:
            return _tap(registry, screen.name, "close-only", "close")
        return None
    if screen.name == "update-game-modal":
        return _tap(registry, screen.name, "close-only", "close")
    if screen.name == "reward-panel":
        # The cobweb-clearing chest variant has the same free-continue
        # semantics as the surveyed reward panel, but its reward card covers
        # the older safe point.  Use the point proved by the live dismiss
        # evidence only when both registered ribbon/chest anchors matched;
        # other reward-panel frames retain the original point.
        element = (
            "safe-continue-cobweb"
            if {
                "reward-panel/signature/anchors/claim-ribbon-left",
                "reward-panel/signature/anchors/mystery-chest-card",
            }.issubset(screen.matched_anchors or ())
            else "safe-continue"
        )
        return _tap(
            registry,
            screen.name,
            element,
            "free-claim",
        )
    if screen.name == "newbie-gift-progress-modal":
        if attempt == 0:
            return _tap(registry, screen.name, "close-only", "close")
        if attempt == 1:
            return _tap(
                registry,
                screen.name,
                "acknowledge-fallback",
                "close",
            )
        return None
    if screen.name == "shop-daily-specials-and-hot-selling":
        # The observed free stock is distinct from the neighbouring gem
        # purchases. Require the positive stock+FREE template, then verify
        # its outcome through the ordinary claim/save evidence path.
        if any(anchor.endswith("/claim-free") for anchor in screen.matched_anchors):
            return _tap(
                registry, screen.name, "claim-highlighted-free-gift", "free-claim"
            )
        return _tap(registry, screen.name, "close", "close")
    if screen.name == "returning-player-gift":
        # An unconditional grant with no close control and no button: the game
        # gives it whether or not it is asked for, and a tap on the reward
        # burst is the only way off the surface.  Nothing here carries a price,
        # a gem cost, an advertisement, a login or an external link.
        return _tap(registry, screen.name, "dismiss", "free-claim")
    return None


def guard_departure_action(
    screen: ScreenIdV1,
    registry: SurfaceRegistry,
) -> PopupActionV1 | None:
    """Close actions only the unattended long-drive guard may spend.

    2026-09-08.  The 13:35Z and 13:40Z long segments opened on the 任务 panel
    a main-task session had left standing (its S2 flow died mid-run), read it
    correctly as ``main-task-panel``, and stopped not_on_board because no
    action was wired for it.  The long drive never starts a main task -- the
    开始 button spends coins and belongs to the S2 flow alone -- so the only
    action registered here is the panel's X, which returns to the restaurant
    scene, from which a verified route back to the board exists.

    This is deliberately not part of ``action_for_screen``: the S2 flow routes
    unexpected surfaces through ``handle_known_popups``, and a panel it just
    tried to start a task from must keep stopping that flow for a ruling
    rather than being silently closed under it.
    """

    if screen.name == "main-task-panel":
        return _tap(registry, screen.name, "close", "close")
    return None


# F4-12 Phase 0, canonical ruling 3.  The main-task flow's popup allowances
# used to be literals and private counters inside the flow; they are declared
# once here and the flow reads them.  Semantics are unchanged.
FLOW_POPUP_SESSION_ACTION_LIMIT = 12


class FlowPopupRuleV1(BaseModel):
    """One flow-side popup allowance: a budget, a one-shot bar, or a degrade."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    surface: str
    # Per-session tap budget on this surface; reaching it stops the flow with
    # ``exhausted_reason`` (a template with one ``{limit}`` placeholder).
    max_taps: int | None = None
    exhausted_reason: str | None = None
    # The surface must not still be up once its action has been spent on it; a
    # reappearance stops the flow with ``reappeared_reason``.
    one_shot: bool = False
    reappeared_reason: str | None = None
    # A claim on this surface stands only while the session has not claimed
    # anything yet; after one free claim anywhere, a further claim here
    # degrades to the surface's close action.
    claim_once: bool = False


FLOW_POPUP_RULES: tuple[FlowPopupRuleV1, ...] = (
    FlowPopupRuleV1(
        surface="newbie-gift",
        max_taps=12,
        exhausted_reason="newbie gift remained active at the {limit}-tap safety limit",
    ),
    FlowPopupRuleV1(
        surface="decoration-choice",
        one_shot=True,
        reappeared_reason=(
            "decoration selection awaits a registered confirmation surface"
        ),
    ),
    FlowPopupRuleV1(
        surface="decoration-confirmation",
        one_shot=True,
        reappeared_reason=(
            "decoration confirmation did not leave its registered surface"
        ),
    ),
    FlowPopupRuleV1(
        surface="shop-daily-specials-and-hot-selling",
        claim_once=True,
    ),
)


def flow_popup_rule(surface: str) -> FlowPopupRuleV1 | None:
    """The declared allowance for one surface, or None when it has none."""

    for rule in FLOW_POPUP_RULES:
        if rule.surface == surface:
            return rule
    return None


class FlowPopupBudget:
    """One main-task session's popup accounting against FLOW_POPUP_RULES."""

    def __init__(self) -> None:
        self.handled = 0
        self.attempts: dict[str, int] = {}
        self.taps: dict[str, int] = {}
        self.acted_surfaces: set[str] = set()
        self.claimed_any = False

    def session_open(self) -> bool:
        return self.handled <= FLOW_POPUP_SESSION_ACTION_LIMIT

    def attempt(self, surface: str) -> int:
        """Attempts already spent on the observed surface this session."""

        return self.attempts.get(surface, 0)

    def stop_reason(self, surface: str) -> str | None:
        """Why this surface must stop the flow now, or None when it may be acted on."""

        rule = flow_popup_rule(surface)
        if rule is None:
            return None
        if rule.one_shot and surface in self.acted_surfaces:
            return rule.reappeared_reason
        if rule.max_taps is not None and self.taps.get(surface, 0) >= rule.max_taps:
            if rule.exhausted_reason is None:
                raise ValueError(f"flow popup rule has a budget but no reason: {surface}")
            return rule.exhausted_reason.format(limit=rule.max_taps)
        return None

    def degrades_to_close(self, popup: PopupActionV1) -> bool:
        """Whether this claim has already been spent and must fall back to close."""

        rule = flow_popup_rule(popup.screen)
        return (
            rule is not None
            and rule.claim_once
            and popup.kind == "free-claim"
            and self.claimed_any
        )

    def record(self, *, observed: str, popup: PopupActionV1, attempt: int) -> None:
        """Account one spent popup action against the session's rules."""

        self.attempts[observed] = attempt + 1
        self.taps[popup.screen] = self.taps.get(popup.screen, 0) + 1
        rule = flow_popup_rule(popup.screen)
        if rule is not None and rule.one_shot:
            self.acted_surfaces.add(popup.screen)
        if popup.kind == "free-claim":
            self.claimed_any = True
        self.handled += 1


def handle_known_popups(
    frame: Any,
    registry: SurfaceRegistry | None = None,
    *,
    attempt: int = 0,
) -> PopupActionV1 | None:
    """Recognize one frame and return a safe action, or ``None`` to stop."""

    selected_registry = registry or SurfaceRegistry()
    screen = selected_registry.identify_screen(frame)
    action = action_for_screen(screen, selected_registry, attempt=attempt)
    if action is not None:
        return action
    if screen.name == "unknown":
        rgb = frame_to_rgb(frame)
        if float(rgb.mean()) <= 2.0 and int(rgb.max()) <= 8:
            return PopupActionV1(
                screen="registered-black-transition",
                kind="wait",
                element=None,
                request=ActionRequestV1(
                    kind="wait",
                    intent="popup:wait:registered-black-transition",
                    params={"ms": 200},
                    capture_pre=False,
                    capture_post=False,
                ),
                safety_basis=(
                    "F4-03a registered a black transition between Start and story"
                ),
            )
    return None


__all__ = [
    "FLOW_POPUP_RULES",
    "FLOW_POPUP_SESSION_ACTION_LIMIT",
    "FlowPopupBudget",
    "FlowPopupRuleV1",
    "PopupActionKind",
    "PopupActionV1",
    "STORY_ADVANCE_FLOW_BUDGET",
    "STORY_ADVANCE_GUARD_BUDGET",
    "STORY_DIALOGUE_SURFACE",
    "action_for_screen",
    "flow_popup_rule",
    "guard_departure_action",
    "handle_known_popups",
    "story_advance_action",
]
