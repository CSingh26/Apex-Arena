// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ThemeToggle } from "@/components/race-rooms/theme-toggle";
import { appRoutes, stripBasePath } from "@/lib/app-paths";
import styles from "./app-navigation.module.css";

type ConnectionState = "connecting" | "live" | "reconnecting" | "degraded";

type AppNavigationProps = {
  contextLabel?: string;
  connection?: ConnectionState;
};

const navigation = [
  { href: appRoutes.home, label: "Home", matches: (pathname: string) => pathname === "/" },
  { href: appRoutes.rooms, label: "Race Rooms", matches: (pathname: string) => pathname.startsWith("/rooms") },
  { href: appRoutes.standings, label: "Standings", matches: (pathname: string) => pathname.startsWith("/standings") },
] as const;

function connectionLabel(connection: ConnectionState): string {
  if (connection === "live") return "Stream connected";
  if (connection === "reconnecting") return "Reconnecting";
  if (connection === "degraded") return "Stream degraded";
  return "Connecting";
}

export function AppNavigation({ contextLabel, connection }: AppNavigationProps) {
  const pathname = usePathname() ?? (
    typeof window === "undefined" ? "/" : stripBasePath(window.location.pathname)
  );
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : menuButtonRef.current;
    const focusable = () => [...(menuRef.current?.querySelectorAll<HTMLElement>("a[href], button:not([disabled])") ?? [])];
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0];
      const last = items.at(-1) ?? first;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.classList.add("nav-menu-open");
    window.requestAnimationFrame(() => focusable()[0]?.focus());
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.classList.remove("nav-menu-open");
      previousFocus?.focus();
    };
  }, [menuOpen]);

  const links = (mobile = false) => <>
    {navigation.map((item) => {
    const active = item.matches(pathname);
    return <Link
      className={`${styles.link} ${active ? styles.active : ""}`}
      href={item.href}
      aria-current={active ? "page" : undefined}
      key={item.href}
      onClick={mobile ? () => setMenuOpen(false) : undefined}
    >{item.label}</Link>;
    })}
    {pathname === "/" && <>
      <a className="app-nav__link" href="#experience" onClick={mobile ? () => setMenuOpen(false) : undefined}>Experience</a>
      <a className="app-nav__link" href="#agents" onClick={mobile ? () => setMenuOpen(false) : undefined}>The room</a>
    </>}
  </>;

  return <header className={styles.shell}>
    <div className={styles.inner}>
      <Link className={styles.brand} href={appRoutes.home} aria-label="Apex Arena home"><i className="brand-mark" /><span>APEX ARENA</span><small>Race intelligence</small></Link>
      <nav className={styles.desktopLinks} aria-label="Primary navigation">{links()}</nav>
      {contextLabel && <span className={styles.context} title={contextLabel}>{contextLabel}</span>}
      <div className={styles.actions}>
        {connection && <span className={`${styles.connection} ${styles[connection]}`} role="status"><span aria-hidden />{connectionLabel(connection)}</span>}
        <ThemeToggle />
        <button ref={menuButtonRef} className={styles.menuButton} type="button" aria-label="Open navigation menu" aria-expanded={menuOpen} aria-controls="mobile-navigation" onClick={() => setMenuOpen(true)}><span aria-hidden /><span aria-hidden /></button>
      </div>
    </div>
    {menuOpen && <div className={styles.mobileLayer}>
      <button className={styles.backdrop} type="button" aria-label="Close navigation menu" onClick={() => setMenuOpen(false)} />
      <nav ref={menuRef} id="mobile-navigation" className={styles.mobileMenu} aria-label="Mobile navigation" role="dialog" aria-modal="true">
        <div><span>Navigate</span><button type="button" aria-label="Close navigation menu" onClick={() => setMenuOpen(false)}>×</button></div>
        {links(true)}
        {contextLabel && <p><span>Current room</span><b>{contextLabel}</b></p>}
      </nav>
    </div>}
  </header>;
}
