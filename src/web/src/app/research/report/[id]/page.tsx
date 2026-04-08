import ReportClient from "./ReportClient";

export function generateStaticParams() {
  return [{ id: "_" }];
}

export default function Page() {
  return <ReportClient />;
}
