import EditorClient from "./EditorClient";

export function generateStaticParams() {
  // Static export requires pre-defined params. Add strategy names here as they are created.
  // In development, Next.js will generate pages on-demand.
  return [{ name: "ema_cross_demo" }];
}

export default function StrategyEditorPage() {
  return <EditorClient />;
}
