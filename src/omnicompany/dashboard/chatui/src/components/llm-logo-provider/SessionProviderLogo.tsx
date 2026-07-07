import type { LLMProvider } from '../../types/app';
import ClaudeLogo from './ClaudeLogo';
import CodexLogo from './CodexLogo';
import CursorLogo from './CursorLogo';
import GeminiLogo from './GeminiLogo';
import OpenCodeLogo from './OpenCodeLogo';
import OmniAgentLogo from './OmniAgentLogo';
import ControllerLogo from './ControllerLogo';

type SessionProviderLogoProps = {
  provider?: LLMProvider | string | null;
  className?: string;
};

export default function SessionProviderLogo({
  provider = 'claude',
  className = 'w-5 h-5',
}: SessionProviderLogoProps) {
  if (provider === 'cursor') {
    return <CursorLogo className={className} />;
  }

  if (provider === 'codex') {
    return <CodexLogo className={className} />;
  }

  if (provider === 'gemini') {
    return <GeminiLogo className={className} />;
  }

  if (provider === 'opencode') {
    return <OpenCodeLogo className={className} />;
  }

  if (provider === 'omni_agent') {
    return <OmniAgentLogo className={className} />;
  }

  if (provider === 'controller') {
    return <ControllerLogo className={className} />;
  }

  return <ClaudeLogo className={className} />;
}
