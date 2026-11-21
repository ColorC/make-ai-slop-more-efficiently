type OmniAgentLogoProps = {
  className?: string;
};

const OmniAgentLogo = ({ className = 'w-5 h-5' }: OmniAgentLogoProps) => (
  <svg
    viewBox="0 0 24 24"
    role="img"
    aria-label="Omni Agent"
    className={className}
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <rect x="2.5" y="2.5" width="19" height="19" rx="4" className="fill-foreground" />
    <circle cx="12" cy="12" r="5.2" className="stroke-background" strokeWidth="1.9" />
    <circle cx="12" cy="12" r="1.7" className="fill-background" />
  </svg>
);

export default OmniAgentLogo;
