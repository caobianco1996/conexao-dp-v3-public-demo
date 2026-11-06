const paths = {
  calendar: "M8 3v4M16 3v4M4 10h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1M8 14h2M14 14h2M8 17h2",
  clock: "M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  alert: "m12 3 10 18H2L12 3M12 9v5M12 17v.1",
  files: "M8 3h8l4 4v13H8V3M16 3v5h4M4 7v15h12M11 12h6M11 16h6",
  pen: "m15 4 5 5M4 20l5-1L21 7l-5-5L4 14v6M3 22h18",
  check: "m8 12 3 3 5-6M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  flag: "M5 22V3M5 4c5-4 9 4 14 0v10c-5 4-9-4-14 0",
  filter: "M4 7h16M7 12h10M10 17h4",
  chevron: "m8 10 4 4 4-4",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  refresh: "M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3",
  info: "M12 11v6M12 7v.1M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  building: "M4 21V3h12v18M16 9h4v12H2M8 7h4M8 11h4M8 15h4M9 21v-3h2v3",
  plug: "M9 3v5M15 3v5M6 8h12v2a6 6 0 0 1-12 0V8M12 16v5",
  settings: "M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M9 15v6",
};

export default function OperationsIcon({ name, className = "" }) {
  return <svg className={`operations-icon ${className}`.trim()} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.files}/></svg>;
}
