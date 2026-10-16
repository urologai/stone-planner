import { useEffect, useState } from "react";
import { api } from "./api";
import Login from "./Login";
import Workspace from "./Workspace";

export default function App() {
  const [state, setState] = useState<"loading" | "login" | "in">("loading");
  const [gpu, setGpu] = useState(false);

  const refresh = () =>
    api
      .session()
      .then((s) => {
        setGpu(s.segmentation_configured);
        setState(s.authenticated ? "in" : "login");
      })
      .catch(() => setState("login"));

  useEffect(() => {
    refresh();
  }, []);

  if (state === "loading") return <div className="center muted">Loading</div>;
  if (state === "login") return <Login onDone={refresh} />;
  return (
    <Workspace
      gpu={gpu}
      onSettingsChanged={refresh}
      onLogout={async () => {
        await api.logout();
        setState("login");
      }}
    />
  );
}
