import { SignalListClient } from "./SignalListClient";

export const metadata = {
  title: "Signals · TinoHelm",
};

/**
 * /signal — signal-run browser.
 *
 * Static-export shell: all data flow lives in ``SignalListClient`` because
 * the app is built with ``output: "export"``.
 */
export default function SignalPage() {
  return <SignalListClient />;
}
