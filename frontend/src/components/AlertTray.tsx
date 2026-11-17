import { useEffect, useRef, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { band } from "./risk";
import { BAND_WORD, Score } from "./Score";
import { usePopover } from "./usePopover";
import type { WardFeed } from "./wardFeed";
import styles from "./AlertTray.module.css";

const ACK = "[data-ack]:not([disabled])";

/**
 * The bell and its tray (kit hw-bell, hw-alert-tray). Opening moves focus to the first
 * Acknowledge, Tab stays inside, Esc closes and gives focus back to the bell. Acknowledging keeps
 * the tray open and moves focus to the next Acknowledge, or to Close when the tray is empty.
 */
export function AlertTray({ feed }: { feed: WardFeed }) {
  const { open, setOpen, anchor, trigger } = usePopover();
  const tray = useRef<HTMLDivElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const refocus = useRef<number | null>(null);
  const navigate = useNavigate();
  const { alerts } = feed;
  const n = alerts.length;
  const offline = feed.failedSince !== null;

  useEffect(() => {
    if (open) (tray.current?.querySelector<HTMLElement>(ACK) ?? close.current)?.focus();
  }, [open]);

  useEffect(() => {
    const i = refocus.current;
    if (i === null || !tray.current) return;
    refocus.current = null;
    const acks = tray.current.querySelectorAll<HTMLElement>(ACK);
    (acks[Math.min(i, acks.length - 1)] ?? close.current)?.focus();
  }, [alerts]);

  const shut = () => {
    setOpen(false);
    trigger.current?.focus();
  };

  // Tab and Shift+Tab go round inside the tray.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab") return;
    const stops = e.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled])");
    const edge = e.shiftKey ? stops[0] : stops[stops.length - 1];
    if (document.activeElement !== edge) return;
    e.preventDefault();
    (e.shiftKey ? stops[stops.length - 1] : stops[0]).focus();
  };

  return (
    <div ref={anchor} className={`hw-menu-anchor ${styles.anchor}`}>
      <button
        ref={trigger}
        type="button"
        className="hw-bell"
        aria-label={`${n} unacknowledged alert${n === 1 ? "" : "s"}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="hw-icon fa-solid fa-bell" aria-hidden="true" />
        {n > 0 && <span className="hw-bell__count" aria-hidden="true">{n}</span>}
      </button>

      {open && (
        <div ref={tray} className="hw-alert-tray" role="dialog" aria-label="Unacknowledged alerts" onKeyDown={onKeyDown}>
          <div className={styles.head}>
            <h2 className={styles.title}>Unacknowledged alerts{feed.data ? ` on Ward ${feed.data.ward}` : ""}</h2>
            <button ref={close} type="button" className="hw-close" aria-label="Close alerts" onClick={shut}>
              <span className="hw-icon fa-solid fa-xmark" aria-hidden="true" />
            </button>
          </div>
          {alerts.map((r, i) => (
            <div key={r.id} className="hw-alert-tray__item">
              <Score score={r.score} band={band(r)} small />
              <div className={styles.who}>
                <span className={styles.name}>{r.name}</span>
                {/* No time: the ward does not say since when an alert has waited. */}
                <span className={styles.line}>Bed {r.bed} &middot; {BAND_WORD[band(r)]}</span>
              </div>
              <button
                type="button"
                className="hw-button hw-button--sm"
                aria-label={`Open ${r.name}`}
                onClick={() => {
                  setOpen(false);
                  navigate(`/patient/${r.id}`);
                }}
              >
                Open
              </button>
              <button
                type="button"
                data-ack
                className="hw-button hw-button--primary hw-button--sm"
                disabled={offline}
                aria-label={`Acknowledge the alert for ${r.name}`}
                onClick={() => {
                  refocus.current = i;
                  feed.acknowledge(r);
                }}
              >
                Acknowledge
              </button>
            </div>
          ))}
          {n === 0 && <p className={styles.empty}>Nothing is waiting. New alerts appear here and on the patient&rsquo;s row.</p>}
          <p className={styles.foot}>BioSentry does not replace a monitor alarm.</p>
        </div>
      )}
    </div>
  );
}
