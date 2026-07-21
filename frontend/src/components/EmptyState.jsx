/**
 * Centered empty-state panel.
 * `icon` is a lucide-react component (e.g. icon={Rocket}).
 */
export default function EmptyState({ icon: Icon, title, hint }) {
  return (
    <div className="panel flex flex-col items-center justify-center px-6 py-14 text-center motion-safe:animate-fade-up">
      {Icon && (
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full border border-trax9-border bg-trax9-border/20">
          <Icon size={24} strokeWidth={1.5} className="text-trax9-muted" />
        </div>
      )}
      <div className="text-sm font-semibold text-trax9-text">{title}</div>
      {hint && <div className="mt-1.5 max-w-sm text-sm text-trax9-muted">{hint}</div>}
    </div>
  );
}
