import React from 'react'
import { SKINS, SKIN_KITS, type SkinDefinition } from '../../skins/skinRegistry'
import { useUiSettings } from '../../stores/uiSettingsStore'

const KIT_LABEL = new Map(SKIN_KITS.map((kit) => [kit.id, kit.label]))

/** 皮肤小样: 用这套皮肤自己的底图、纸面、线条、主操作色画一张缩微界面。 */
function SkinSwatch({ skin }: { skin: SkinDefinition }) {
  const p = skin.preview
  const scene: React.CSSProperties = {
    backgroundColor: p.scene,
    backgroundImage: p.pattern,
  }
  const paper: React.CSSProperties = {
    background: p.paper,
    border: `${p.lineWidth} solid ${p.line}`,
    borderRadius: p.radius,
    boxShadow: p.shadow,
  }
  return (
    <span className="st-skin-swatch" style={scene} aria-hidden="true">
      <span className="st-skin-paper" style={paper}>
        <span className="st-skin-ink" style={{ background: p.ink }} />
        <span className="st-skin-ink short" style={{ background: p.mutedInk }} />
        <span className="st-skin-row">
          <span
            className="st-skin-chip"
            style={{ background: p.primary, color: p.primaryInk, borderRadius: p.radius, border: `${p.lineWidth} solid ${p.line}` }}
          >
            Aa
          </span>
          <span className="st-skin-dot" style={{ background: p.focus }} />
        </span>
      </span>
    </span>
  )
}

export default function SkinPicker() {
  const current = useUiSettings((state) => state.skin)
  const setSkin = useUiSettings((state) => state.setSkin)
  return (
    <div className="st-skin-grid" role="radiogroup" aria-label="皮肤" data-testid="settings-skin-picker">
      {SKINS.map((skin) => {
        const selected = skin.id === current
        return (
          <button
            key={skin.id}
            type="button"
            role="radio"
            aria-checked={selected}
            className={`st-skin-card${selected ? ' on' : ''}`}
            data-testid={`settings-skin-${skin.id}`}
            onClick={() => setSkin(skin.id)}
          >
            <SkinSwatch skin={skin} />
            <span className="st-skin-name">{skin.label}</span>
            <span className="st-skin-kit">{KIT_LABEL.get(skin.kit) ?? skin.kit}</span>
          </button>
        )
      })}
    </div>
  )
}
