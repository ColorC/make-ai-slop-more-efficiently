type ControllerLogoProps = {
  className?: string;
};

// The controller (总控) is the local Claude runtime with a 总控 prompt; its mark
// nods to a "command/hub" radial so it reads distinctly from the plain Claude
// sessions in the sidebar.
const ControllerLogo = ({ className = 'w-5 h-5' }: ControllerLogoProps) => (
  <svg
    viewBox="0 0 24 24"
    role="img"
    aria-label="总控"
    className={className}
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <circle cx="12" cy="12" r="9.5" className="fill-foreground" />
    <circle cx="12" cy="12" r="3" className="fill-background" />
    <path
      d="M12 2.5V6M12 18V21.5M2.5 12H6M18 12H21.5"
      className="stroke-background"
      strokeWidth="1.8"
      strokeLinecap="round"
    />
  </svg>
);

export default ControllerLogo;
