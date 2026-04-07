export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-3 font-mono text-[0.55rem] tracking-widest uppercase text-primary">
      {children}
      <div className="flex-1 h-px bg-border" />
    </div>
  );
}
