const base = {
  width: 18,
  height: 18,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
};

export const MenuIcon = () => (
  <svg {...base}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </svg>
);

export const CloseIcon = () => (
  <svg {...base}>
    <path d="M18 6 6 18M6 6l12 12" />
  </svg>
);

export const SendIcon = () => (
  <svg {...base}>
    <path d="M12 19V5M5 12l7-7 7 7" />
  </svg>
);

export const PlusIcon = () => (
  <svg {...base}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);

export const RefreshIcon = () => (
  <svg {...base}>
    <path d="M21 12a9 9 0 1 1-2.64-6.36L21 8" />
    <path d="M21 3v5h-5" />
  </svg>
);

export const FileIcon = () => (
  <svg {...base} width={14} height={14}>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5" />
  </svg>
);

export const ChevronIcon = () => (
  <svg {...base} width={14} height={14}>
    <path d="m9 6 6 6-6 6" />
  </svg>
);

export const MicIcon = () => (
  <svg {...base}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
  </svg>
);

export const WaveIcon = () => (
  <svg {...base}>
    <path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4" />
  </svg>
);

export const SpeakerIcon = () => (
  <svg {...base} width={16} height={16}>
    <path d="M11 5 6 9H3v6h3l5 4z" />
    <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />
  </svg>
);

export const StopIcon = () => (
  <svg {...base} width={16} height={16}>
    <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />
  </svg>
);

export const LogoMark = () => (
  <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
    <rect width="32" height="32" rx="8" fill="var(--accent)" />
    <path
      d="M9 11.5A2.5 2.5 0 0 1 11.5 9h9a2.5 2.5 0 0 1 2.5 2.5v6a2.5 2.5 0 0 1-2.5 2.5H15l-4 3.5V20h0.5A2.5 2.5 0 0 1 9 17.5z"
      fill="var(--accent-contrast)"
    />
  </svg>
);
