// Simple stroke icons, one per node type.
const PATHS: Record<string, string> = {
  input: "M3 10h10M9 6l4 4-4 4M16 4v12",
  output: "M4 4v12M7 10h10M13 6l4 4-4 4",
  router: "M3 10h5M8 10c3 0 3-5 6-5h3M8 10c3 0 3 5 6 5h3M15 3l2 2-2 2M15 13l2 2-2 2",
  agent: "M6 8a4 4 0 0 1 8 0v4a4 4 0 0 1-8 0zM8.5 9.5v1M11.5 9.5v1M10 2v2M3 10h3M14 10h3",
  tool: "M12.5 3.5a3.5 3.5 0 0 0-3.3 4.7L4 13.4 6.6 16l5.2-5.2a3.5 3.5 0 0 0 4.7-3.3l-2.2 2.2-2-.6-.6-2z",
  context: "M5 3h7l3 3v11H5zM12 3v3h3M7.5 9.5h5M7.5 12.5h5",
  policy: "M10 2.5l6 2.2v4.6c0 3.6-2.4 6.3-6 8.2-3.6-1.9-6-4.6-6-8.2V4.7zM7.5 10l1.8 1.8L12.8 8",
  meta: "M15.5 8A6 6 0 0 0 4.6 6.5M4.5 12a6 6 0 0 0 10.9 1.5M4.5 3.5v3h3M15.5 16.5v-3h-3",
};

export function Icon({ type }: { type: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[type] ?? PATHS.agent} />
    </svg>
  );
}
