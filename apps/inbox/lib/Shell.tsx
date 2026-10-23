import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState, type ReactNode } from "react";
import { signIn, signedIn, who } from "./session";

/** Layout plus the sign-in gate: nothing below renders until there is a session. */
export function Shell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!signedIn()) {
      void signIn();
    } else {
      setReady(true);
    }
  }, []);
  if (!ready) return <main className="muted">Signing you in...</main>;
  const me = who();
  const tab = (href: string, label: string) => (
    <Link href={href} className={router.pathname === href ? "active" : ""}>{label}</Link>
  );
  return (
    <>
      <header>
        <strong>Front Desk</strong>
        {tab("/", "Handed to staff")}
        {me?.owner ? tab("/insights", "Insights") : null}
        <span className="who">{me?.email}</span>
      </header>
      <main>{children}</main>
    </>
  );
}
