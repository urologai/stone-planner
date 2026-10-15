import { useEffect, useState } from "react";
import { api } from "./api";
import Login from "./Login";
import Workspace from "./Workspace";

export default function App() {
  const [state, setState] = useState<"loading" | "login" | "in">("loading");
  const [gpu, setGpu] = useState(false);

  useEffect(() => {
    api
      .session()
      .then((s) => {
        setGpu(s.segmentation_configured);
        setState(s.authenticated ? "in" : "login");
      })
      .catch(() => setState("login"));
  }, []);

  if (state === "loading") return <div className="center muted">Loading</div>;
  if (state === "login") return <Login onDone={() => setState("in")} />;
  return (
    <Workspace
      gpu={gpu}
      onLogout={async () => {
        await api.logout();
        setState("login");
      }}
    />
  );
}
