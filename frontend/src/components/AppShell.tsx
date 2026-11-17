import { useId, useSyncExternalStore, type ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import type { Session } from "../types";
import { AlertTray } from "./AlertTray";
import { PatientSearch } from "./PatientSearch";
import { asksWard, useAssistant } from "./roles";
import { formatClock } from "./format";
import { AccountMenu } from "./SessionMenu";
import { usePopover } from "./usePopover";
import { useWardFeed } from "./wardFeed";
import { since as wardTime } from "./wardNow";
import styles from "./AppShell.module.css";

interface Props {
  session: Session;
  onSignOut: (explicit?: boolean) => void;
  /** What this page is (e.g. a patient's name): the phone's app bar shows it. Plain text: the page keeps its own h1. */
  title?: ReactNode;
  /** Live facts the page passes in: connection status and the ward clock. */
  status?: ReactNode;
  /** A detail screen: on a phone its own action bar replaces the bottom tabs (a screen never shows both). */
  detail?: boolean;
  children: ReactNode;
}

export function Mark() {
  return (
    <span className={styles.mark}>
      <span className={styles.markIcon} aria-hidden="true"><span className="fa-solid fa-heart-pulse" /></span>
      <span className={styles.markWord}>BioSentry</span>
    </span>
  );
}

// ---- Where each role can go ----

interface Tab { label: string; to: string; icon: string; current: (path: string, tab: string | null) => boolean }

const WARD: Tab = { label: "Ward", to: "/ward", icon: "list-ol", current: (p) => p === "/ward" };
// Until Census and Admit have their own pages, each opens the Station on its matching tab.
const CENSUS: Tab = { label: "Census", to: "/station?tab=flow", icon: "chart-column", current: (p, t) => p === "/station" && t === "flow" };
const ADMIT: Tab = { label: "Admit", to: "/station", icon: "user-plus", current: (p, t) => p === "/station" && t !== "flow" };
const OVERVIEW: Tab = { label: "Overview", to: "/overview", icon: "gauge-high", current: (p) => p === "/overview" };

const TABS: Record<string, readonly Tab[]> = { md: [WARD, CENSUS, ADMIT], charge_rn: [WARD, CENSUS, ADMIT], admin: [OVERVIEW, WARD] };

interface Item { title: string; sub: string; to: string }

const CLINICAL_RESEARCH: readonly Item[] = [
  { title: "Ask the assistant", sub: "Questions about the ward", to: "/ask" },
  { title: "What-if runs", sub: "Change a rule and compare the result", to: "/what-if" },
  { title: "Ward research", sub: "Every patient against their own baseline", to: "/ward/research" },
];
const LAB: readonly Item[] = [
  { title: "Research numbers", sub: "What the held-out trials show", to: "/lab/research" },
  { title: "Pipeline", sub: "The eight stages, and which ones touch the ranking", to: "/lab/pipeline" },
  { title: "Simulation lab", sub: "Simulated runs", to: "/lab" },
];

/**
 * The research routes this role can open in this build, as App.tsx gates them: a physician or the
 * charge nurse with the API asks, runs what-ifs and reads ward research; an administrator, and every
 * demo role of the fixture build, reads the lab. A staff nurse has no menu. The build switch is read
 * here, as AccountMenu reads it, so the lab pages never reach the api module.
 */
export function researchItems(role: string): readonly Item[] {
  if (!(role in TABS)) return [];
  const live = Boolean(import.meta.env.VITE_API_URL);
  return asksWard(role as Session["role"]) && live ? CLINICAL_RESEARCH : LAB;
}

const ANALYTICS_PATH = /^\/(?![/\\])[^\s\\]*$/; // same-origin path only: "//x" and "/\x" leave the site

const PHONE = "(width < 768px)";
const onMedia = (change: () => void) => {
  const m = window.matchMedia(PHONE);
  m.addEventListener?.("change", change);
  return () => m.removeEventListener?.("change", change);
};
/** The phone layout (under 768px), as the bar decides it: pages that change shape on a phone read the same. */
export const usePhone = () => useSyncExternalStore(onMedia, () => window.matchMedia(PHONE).matches);

// ---- Pieces of the bar ----

/** The Research (or Lab) menu: a disclosure of links under a tinted note. Esc closes it and gives focus back. */
function ResearchMenu({ label, items, bottom = false }: { label: string; items: readonly Item[]; bottom?: boolean }) {
  const { open, setOpen, anchor, trigger } = usePopover();
  const id = useId();
  return (
    <div ref={anchor} className={`hw-menu-anchor ${styles.menuAnchor}`}>
      <button
        ref={trigger}
        type="button"
        className={bottom ? `hw-bottomnav__item ${styles.bottomTrigger}` : `hw-header__link ${styles.menuTrigger}`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`${bottom ? "hw-bottomnav__icon " : ""}hw-icon fa-solid fa-flask`} aria-hidden="true" />
        {label}
        {!bottom && <span className="hw-icon fa-solid fa-chevron-down" aria-hidden="true" />}
      </button>
      {open && (
        <div id={id} className={`hw-menu ${styles.research} ${bottom ? styles.up : ""}`} data-align={bottom ? "end" : undefined}>
          <p className={`hw-alert ${styles.researchNote}`}>
            <span className="hw-alert__icon hw-icon fa-solid fa-flask" aria-hidden="true" />
            <span className="hw-alert__body">Research space. Synthetic, not validated. Nothing here changes a patient&rsquo;s rank.</span>
          </p>
          {items.map((it) => (
            <Link key={it.to} to={it.to} className="hw-menu__item" onClick={() => setOpen(false)}>
              <span className={styles.item}>
                <span className={styles.itemTitle}>{it.title}</span>{" "}
                <span className={styles.itemSub}>{it.sub}</span>
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

/** Switches the unit a clinician watches. Shown only to someone with more than one unit. */
export function UnitSwitcher({ units, current, onPick }: { units: readonly string[]; current: string; onPick?: (unit: string) => void }) {
  const { open, setOpen, anchor, trigger } = usePopover();
  const id = useId();
  if (units.length < 2) return null;
  return (
    <div ref={anchor} className="hw-menu-anchor">
      <button ref={trigger} type="button" className="hw-button hw-button--sm" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
        Ward {current}
        <span className="hw-icon fa-solid fa-chevron-down" aria-hidden="true" />
      </button>
      {open && (
        <div id={id} className="hw-menu" aria-label="Units">
          {units.map((u) => (
            <button
              key={u}
              type="button"
              className="hw-menu__item"
              aria-current={u === current ? "true" : undefined}
              onClick={() => {
                setOpen(false);
                onPick?.(u);
              }}
            >
              Ward {u}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The offline banner (kit hw-banner--inverse): black, so it borrows no clinical colour. `since` is the browser
 * time of the first failed read, `from` the ward time of the last good one; `acts` adds that the writes wait.
 */
export function OfflineBanner({ since, from, retry, acts, className = "" }: {
  since: number; from: number | null; retry: string; acts: boolean; className?: string;
}) {
  return (
    <div className={`hw-banner hw-banner--inverse ${className}`} role="alert">
      <span className="hw-icon fa-solid fa-ban" aria-hidden="true" />
      <strong className="hw-banner__title">Offline since {formatClock(wardTime(since))}</strong>
      <span>
        Cannot reach the ward server. Showing data from {from ? formatClock(from) : "--"}; ages below stand at that time. {retry}
        {acts && " Acknowledging and recording wait until the connection is back."}
      </span>
    </div>
  );
}

/**
 * The frame of every signed-in page: one top bar (mark, the role's tabs, unit, search, status, bell,
 * account) over the page. On a phone (< 768px) the bar is an app bar with the page's title and status,
 * and the tabs move to a bottom bar; a staff nurse, who has no tabs, has none.
 */
export function AppShell({ session, onSignOut, title, status, detail = false, children }: Props) {
  const phone = usePhone();
  const feed = useWardFeed();
  const assistant = useAssistant();
  const { pathname } = useLocation();
  const [params] = useSearchParams();
  const tab = params.get("tab");
  const tabs = TABS[session.role] ?? [];
  const items = researchItems(session.role);
  const menuLabel = session.role === "admin" ? "Lab" : "Research";
  const analytics: unknown = import.meta.env.VITE_ANALYTICS_URL;
  const analyticsUrl = typeof analytics === "string" && ANALYTICS_PATH.test(analytics) ? analytics : null;
  const bottomNav = phone && tabs.length > 0 && !detail;

  const tabLinks = (className: string, icon: boolean) =>
    tabs.map((t) => (
      <Link key={t.label} to={t.to} className={className} aria-current={t.current(pathname, tab) ? "page" : undefined}>
        {icon && <span className={`hw-bottomnav__icon hw-icon fa-solid fa-${t.icon}`} aria-hidden="true" />}
        {t.label}
      </Link>
    ));

  return (
    <div className={`${styles.shell} ${bottomNav ? "hw-has-bottomnav" : ""}`}>
      <header className={styles.bar}>
        <Link to="/ward" className={styles.home} aria-label="BioSentry, go to the ward list"><Mark /></Link>

        {phone ? (
          <div className={styles.heading}>
            <span className={styles.title}>{title ?? `Ward ${session.ward}`}</span>
            {status && <span className={styles.status}>{status}</span>}
          </div>
        ) : (
          <>
            {tabs.length === 0 && <span className={styles.title}>Ward {session.ward}</span>}
            {(tabs.length > 0 || analyticsUrl) && (
              <nav aria-label="Main" className={`hw-header__nav ${styles.tabs}`}>
                {tabLinks("hw-header__link", false)}
                {items.length > 0 && <ResearchMenu label={menuLabel} items={items} />}
                {analyticsUrl && (
                  <a href={analyticsUrl} target="_blank" rel="noopener noreferrer" className="hw-header__link">Analytics</a>
                )}
              </nav>
            )}
          </>
        )}

        <div className={styles.tools}>
          {!phone && (
            // ponytail: a session holds one ward, so this never shows; pass the units and onPick once sessions carry several.
            <UnitSwitcher units={[session.ward]} current={session.ward} />
          )}
          {!phone && feed?.data && <PatientSearch ward={feed.data} />}
          {!phone && status && <span className={styles.status}>{status}</span>}
          {assistant && (
            // Desk: icon and word. Phone: an icon in the app bar, named for a screen reader.
            <button
              type="button"
              className={phone ? "hw-appbar__action" : "hw-button hw-button--sm"}
              aria-label={phone ? "Ask the assistant" : undefined}
              aria-haspopup="dialog"
              aria-expanded={assistant.open}
              onClick={(e) => assistant.toggle(e.currentTarget)}
            >
              <span className="hw-icon fa-solid fa-comments" aria-hidden="true" />
              {!phone && "Assistant"}
            </button>
          )}
          {feed && !feed.readOnly && <AlertTray feed={feed} />}
          <AccountMenu session={session} onSignOut={onSignOut} />
        </div>
      </header>

      <main className={styles.main}>
        {feed && feed.notice !== null && (
          <p className={`hw-alert ${styles.notice}`} role="status">
            <span className="hw-alert__body">{feed.notice}</span>{" "}
            <button type="button" className="hw-button hw-button--ghost hw-button--sm" onClick={() => feed.setNotice(null)}>
              Dismiss
            </button>
          </p>
        )}
        {children}
      </main>
      <p className={styles.credit}>Set in HarmonyOS Sans</p>

      {bottomNav && (
        <nav aria-label="Main" className="hw-bottomnav">
          {tabLinks("hw-bottomnav__item", true)}
          {items.length > 0 && <ResearchMenu label={menuLabel} items={items} bottom />}
        </nav>
      )}
    </div>
  );
}
