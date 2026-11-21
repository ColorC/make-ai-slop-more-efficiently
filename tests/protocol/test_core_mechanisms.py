"""Core mechanism verification tests — proving theoretical predictions.

Tests:
  1. Pain propagation directionality (P1-E1)  [skipped: depends on retired RouteGraph]
  2. Boltzmann routing math correctness (T2.1-T2.5)
"""

import sys, os, math, tempfile
import pytest
sys.path.insert(0, "src")

from datetime import datetime, timezone


# ============================================================
# Test 1: Pain Propagation Directionality (P1-E1)
# ============================================================

@pytest.mark.skip(reason="依赖已退役的 RouteGraph/IntentNode")
def test_pain_propagation_directionality():
    """Theory (03§2.3): Causal backpropagation — blame attenuates with γ^depth.
    
    Construct trace: A → B → C, failure at C.
    Expected: pain(C) > pain(B) > pain(A), ratios ≈ 1 : γ : γ²
    """
    from omnicompany.runtime.route_graph import RouteGraph, IntentNode
    from omnicompany.runtime.signals.pain_system import PainEvent, PainPropagator

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        rg = RouteGraph(db_path)
        now = datetime.now(timezone.utc).isoformat()

        for nid in ["node_A", "node_B", "node_C"]:
            rg.upsert_node(IntentNode(
                node_id=nid, input_types=["x"], output_types=["y"],
                action_class="execute", canonical_desc="test",
                hit_count=5, tool_name="bash",
                created_at=now, last_seen=now,
            ))

        propagator = PainPropagator(rg)
        propagator.GAMMA_DECAY = 0.5

        event = PainEvent(
            source_trace_id="t1", source_step_num=3,
            node_id="node_C", pain_intensity=0.8,
            irrecoverability=0.3, pain_tier=2,
            propagate_depth=-1,  # full chain
        )

        trace_steps = [
            {"step_num": 1, "route_node_id": "node_A"},
            {"step_num": 2, "route_node_id": "node_B"},
            {"step_num": 3, "route_node_id": "node_C"},
        ]

        updated = propagator.propagate(event, trace_steps)

        pain_C = rg.get_node("node_C").pain_score
        pain_B = rg.get_node("node_B").pain_score
        pain_A = rg.get_node("node_A").pain_score

        assert pain_C > pain_B > pain_A, \
            f"Direction violated: C={pain_C:.4f}, B={pain_B:.4f}, A={pain_A:.4f}"

        ratio_BA = pain_B / pain_C if pain_C > 0 else 0
        ratio_AB = pain_A / pain_C if pain_C > 0 else 0

        # pain_C = accumulate(0, 0.8) = min(1, 0 + 0.8*(1-0)) = 0.8
        # pain_B = accumulate(0, 0.8*0.5) = min(1, 0 + 0.4*(1-0)) = 0.4
        # pain_A = accumulate(0, 0.8*0.25) = min(1, 0 + 0.2*(1-0)) = 0.2
        assert abs(pain_C - 0.8) < 0.01, f"pain_C={pain_C:.4f}, expected ~0.8"
        assert abs(pain_B - 0.4) < 0.01, f"pain_B={pain_B:.4f}, expected ~0.4"
        assert abs(pain_A - 0.2) < 0.01, f"pain_A={pain_A:.4f}, expected ~0.2"

        print("PAIN PROPAGATION TEST PASS")
        print(f"  pain(C)={pain_C:.4f}, pain(B)={pain_B:.4f}, pain(A)={pain_A:.4f}")
        print(f"  ratio B/C={ratio_BA:.2f} (expect ~0.50)")
        print(f"  ratio A/C={ratio_AB:.2f} (expect ~0.25)")
        rg.close()


@pytest.mark.skip(reason="依赖已退役的 RouteGraph/IntentNode")
def test_pain_heal_mechanism():
    """Theory (N8.6.3): Successful traversal heals — ~22 successes → pain < 10%."""
    from omnicompany.runtime.route_graph import RouteGraph, IntentNode
    from omnicompany.runtime.signals.pain_system import PainPropagator

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        rg = RouteGraph(db_path)
        now = datetime.now(timezone.utc).isoformat()

        rg.upsert_node(IntentNode(
            node_id="test_node", input_types=["x"], output_types=["y"],
            action_class="execute", canonical_desc="test",
            hit_count=5, tool_name="bash",
            created_at=now, last_seen=now, pain_score=0.65,
        ))

        propagator = PainPropagator(rg)

        # Single heal
        propagator.heal("test_node", heal_rate=0.10)
        after_1 = rg.get_node("test_node").pain_score
        assert abs(after_1 - 0.585) < 0.001, f"After 1 heal: {after_1:.4f}, expected 0.585"

        # Heal 21 more times (total 22)
        for _ in range(21):
            propagator.heal("test_node", heal_rate=0.10)
        after_22 = rg.get_node("test_node").pain_score

        assert after_22 < 0.10, f"After 22 heals: {after_22:.4f}, should be < 0.10"

        print("PAIN HEAL TEST PASS")
        print(f"  Initial: 0.65 → After 1 heal: {after_1:.4f} → After 22 heals: {after_22:.4f}")
        rg.close()


# ============================================================
# Test 2: Boltzmann Routing Math (T2.1-T2.5)
# ============================================================

def test_boltzmann_math_correctness():
    """Theory (03§2.4): P(c) = exp(-β·P_c)·S_c / Σ exp(-β·P_i)·S_i"""
    from omnicompany.runtime.routing.boltzmann_router import BoltzmannRouter, RouteCandidate

    candidates = [
        RouteCandidate(node_id="c1", pain_score=0.0, success_rate=0.8, hit_count=10, deprecated=False, hard_eliminated=False),
        RouteCandidate(node_id="c2", pain_score=0.5, success_rate=0.6, hit_count=10, deprecated=False, hard_eliminated=False),
        RouteCandidate(node_id="c3", pain_score=0.9, success_rate=0.3, hit_count=10, deprecated=False, hard_eliminated=False),
    ]

    router = BoltzmannRouter(beta=2.0)

    # Compute theoretical probabilities
    weights = []
    for c in candidates:
        w = math.exp(-2.0 * c.pain_score) * max(c.success_rate, 0.01)
        weights.append(w)
    total = sum(weights)
    expected_probs = [w / total for w in weights]

    # P(c1) should be highest (low pain, high success)
    assert expected_probs[0] > expected_probs[1] > expected_probs[2], \
        f"Expected P(c1) > P(c2) > P(c3), got {expected_probs}"

    # Σ P(ci) = 1
    assert abs(sum(expected_probs) - 1.0) < 1e-10, f"Probs don't sum to 1: {sum(expected_probs)}"

    # Empirical frequency test (1000 trials)
    counts = {"c1": 0, "c2": 0, "c3": 0}
    N = 10000
    for _ in range(N):
        chosen = router.select(candidates)
        counts[chosen.node_id] += 1

    empirical = {k: v / N for k, v in counts.items()}

    # Check empirical matches theoretical (within 5% tolerance)
    for i, nid in enumerate(["c1", "c2", "c3"]):
        diff = abs(empirical[nid] - expected_probs[i])
        assert diff < 0.05, f"{nid}: empirical={empirical[nid]:.3f}, expected={expected_probs[i]:.3f}, diff={diff:.3f}"

    print("BOLTZMANN MATH TEST PASS")
    print(f"  Theoretical: c1={expected_probs[0]:.3f}, c2={expected_probs[1]:.3f}, c3={expected_probs[2]:.3f}")
    print(f"  Empirical (N={N}): c1={empirical['c1']:.3f}, c2={empirical['c2']:.3f}, c3={empirical['c3']:.3f}")


def test_boltzmann_pure_exploration():
    """T2.2: β=0 → all candidates approximately uniform."""
    from omnicompany.runtime.routing.boltzmann_router import BoltzmannRouter, RouteCandidate

    candidates = [
        RouteCandidate(node_id=f"c{i}", pain_score=i * 0.3, success_rate=0.5, hit_count=10, deprecated=False, hard_eliminated=False)
        for i in range(3)
    ]

    router = BoltzmannRouter(beta=0.0)
    N = 10000
    counts = {f"c{i}": 0 for i in range(3)}
    for _ in range(N):
        chosen = router.select(candidates)
        counts[chosen.node_id] += 1

    empirical = {k: v / N for k, v in counts.items()}
    expected = 1.0 / 3

    for nid, freq in empirical.items():
        assert abs(freq - expected) < 0.05, f"{nid}: {freq:.3f} not ≈ {expected:.3f}"

    print("BOLTZMANN EXPLORATION TEST PASS (β=0 → uniform)")
    print(f"  Frequencies: {', '.join(f'{k}={v:.3f}' for k, v in empirical.items())}")


def test_boltzmann_pure_exploitation():
    """T2.3: β=10 → almost deterministic selection of best candidate."""
    from omnicompany.runtime.routing.boltzmann_router import BoltzmannRouter, RouteCandidate

    candidates = [
        RouteCandidate(node_id="best", pain_score=0.0, success_rate=0.9, hit_count=10, deprecated=False, hard_eliminated=False),
        RouteCandidate(node_id="mid", pain_score=0.5, success_rate=0.5, hit_count=10, deprecated=False, hard_eliminated=False),
        RouteCandidate(node_id="worst", pain_score=0.9, success_rate=0.1, hit_count=10, deprecated=False, hard_eliminated=False),
    ]

    router = BoltzmannRouter(beta=10.0)
    N = 1000
    counts = {"best": 0, "mid": 0, "worst": 0}
    for _ in range(N):
        chosen = router.select(candidates)
        counts[chosen.node_id] += 1

    assert counts["best"] / N > 0.95, f"best selected only {counts['best']/N:.2%}, expected >95%"

    print("BOLTZMANN EXPLOITATION TEST PASS (β=10 → deterministic)")
    print(f"  Frequencies: {', '.join(f'{k}={v/N:.3f}' for k, v in counts.items())}")


def test_boltzmann_deprecated_filtered():
    """T2.4/T2.5: deprecated and hard_eliminated nodes never selected."""
    from omnicompany.runtime.routing.boltzmann_router import BoltzmannRouter, RouteCandidate

    candidates = [
        RouteCandidate(node_id="active", pain_score=0.5, success_rate=0.3, hit_count=10, deprecated=False, hard_eliminated=False),
        RouteCandidate(node_id="dep", pain_score=0.0, success_rate=0.9, hit_count=10, deprecated=True, hard_eliminated=False),
        RouteCandidate(node_id="elim", pain_score=0.0, success_rate=1.0, hit_count=10, deprecated=False, hard_eliminated=True),
    ]

    router = BoltzmannRouter(beta=2.0)
    for _ in range(100):
        chosen = router.select(candidates)
        assert chosen.node_id == "active", f"Deprecated/eliminated node selected: {chosen.node_id}"

    print("BOLTZMANN FILTER TEST PASS: deprecated/eliminated never selected")


if __name__ == "__main__":
    tests = [
        test_pain_propagation_directionality,
        test_pain_heal_mechanism,
        test_boltzmann_math_correctness,
        test_boltzmann_pure_exploration,
        test_boltzmann_pure_exploitation,
        test_boltzmann_deprecated_filtered,
        test_crystallization_recommendation,
        test_crystallizer_evaluate,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            print(f"\n{'='*60}")
            print(f"Running: {t.__name__}")
            print(f"{'='*60}")
            t()
            passed += 1
        except Exception as e:
            import traceback
            print(f"FAIL: {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"Core Mechanisms: {passed} passed, {failed} failed out of {len(tests)}")
    if failed == 0:
        print("ALL CORE MECHANISM TESTS PASSED")
