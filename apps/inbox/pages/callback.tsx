import { useEffect, useState } from "react";
import { useRouter } from "next/router";
import { completeSignIn } from "../lib/session";

export default function Callback() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const code = q.get("code");
    const state = q.get("state");
    if (!code || !state) {
      setError("missing sign-in code");
      return;
    }
    completeSignIn(code, state)
      .then((back) => router.replace(back.replace(/^\/inbox/, "") || "/"))
      .catch((e: Error) => setError(e.message));
  }, [router]);
  return <main>{error ? <p className="error">{error}</p> : <p className="muted">Finishing sign-in...</p>}</main>;
}
