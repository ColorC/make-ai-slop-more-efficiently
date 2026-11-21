import React from 'react'
import { SOFT_KEY_SEQUENCES, type SoftKeyId } from './softKeySequences'
import './TerminalSoftKeys.css'

interface SoftKeyDef {
  id: SoftKeyId
  label: string
  ariaLabel: string
}

const KEYS: SoftKeyDef[] = [
  { id: 'esc', label: 'Esc', ariaLabel: 'Escape' },
  { id: 'tab', label: 'Tab', ariaLabel: 'Tab' },
  { id: 'ctrl', label: 'Ctrl', ariaLabel: 'Ctrl（粘滞：点亮后与下一个字符键合成）' },
  { id: 'left', label: '←', ariaLabel: '左方向键' },
  { id: 'down', label: '↓', ariaLabel: '下方向键' },
  { id: 'up', label: '↑', ariaLabel: '上方向键' },
  { id: 'right', label: '→', ariaLabel: '右方向键' },
  { id: 'pgup', label: 'PgUp', ariaLabel: 'Page Up' },
  { id: 'pgdn', label: 'PgDn', ariaLabel: 'Page Down' },
  { id: 'enter', label: 'Enter', ariaLabel: 'Enter' },
]

export interface TerminalSoftKeysProps {
  /** Whether the sticky Ctrl toggle is currently armed (lit). */
  ctrlArmed: boolean
  /** Flip the sticky Ctrl toggle. */
  onToggleCtrl: () => void
  /** Send a resolved control sequence through the terminal's input channel. */
  onSend: (sequence: string) => void
}

export function shouldShowTerminalSoftKeys(
  touchMode: boolean,
  inputFocused: boolean,
  desktopHosted = false,
): boolean {
  return touchMode && inputFocused && !desktopHosted
}

/**
 * Compact software key row for touch terminals: xterm's hidden textarea gives
 * a mobile IME no way to produce Esc/Tab/Ctrl/arrows/PgUp/PgDn, so these ship
 * as explicit buttons at the top of the terminal container. Editor mounts the
 * row only while a touch terminal's hidden textarea owns input focus.
 */
export default function TerminalSoftKeys({ ctrlArmed, onToggleCtrl, onSend }: TerminalSoftKeysProps) {
  // Buttons must never steal focus from xterm's hidden textarea: losing it
  // dismisses the on-screen keyboard on every tap.
  const holdFocus = (event: React.SyntheticEvent) => { event.preventDefault() }

  return (
    <div className="cs-softkeys" data-testid="cc-terminal-softkeys" role="toolbar" aria-label="终端软按键">
      {KEYS.map((key) => {
        const active = key.id === 'ctrl' && ctrlArmed
        return (
          <button
            key={key.id}
            type="button"
            className={`cs-softkey${active ? ' cs-softkey-active' : ''}`}
            aria-label={key.ariaLabel}
            aria-pressed={key.id === 'ctrl' ? ctrlArmed : undefined}
            onPointerDown={holdFocus}
            onMouseDown={holdFocus}
            onClick={() => {
              if (key.id === 'ctrl') onToggleCtrl()
              else onSend(SOFT_KEY_SEQUENCES[key.id])
            }}
          >
            {key.label}
          </button>
        )
      })}
    </div>
  )
}
