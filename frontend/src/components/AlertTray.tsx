import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { cancelLeave, leave } from "hw-ui";
import { band } from "./risk";
import { BAND_WORD, Score } from "./Score";
import { usePopover } from "./usePopover";
import type { WardFeed } from "./wardFeed";

const ACK = "[data-ack]:not([disabled])";

/**
 * The bell and its tray (kit hw-bell, hw-alert-tray). Opening moves focus to the first
 * Acknowledge, Tab stays inside, Esc closes and gives focus back to the bell. Acknowledging keeps
 * the tray open and moves focus to the next Acknowledge, or to Close when the tray is empty.
 */
export function AlertTray({ feed }: { feed: WardFeed }) {
  const { open, setOpen, anchor, trigger } = usePopover();
  // The tray stays on screen through its exit: [hidden] only once leave() has played it.
  const [leaving, setLeaving] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    setLeaving(!open);
  }
  const tray = useRef<HTMLDivElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const refocus = useRef<number | null>(null);
  const navigate = useNavigate();
  const trayId = useId();
  const { alerts } = feed;
  const n = alerts.length;
  const offline = feed.failedSince !== null;

  useEffect(() => {
    const el = tray.current;
    if (open) {
      cancelLeave(el);
      (el?.querySelector<HTMLElement>(ACK) ?? close.current)?.focus();
      return;
    }
    if (!leaving) return;
    let current = true;
    void leave(el).then((done) => {
      if (current && done) setLeaving(false);
    });
    return () => {
      current = false;
    };
  }, [open, leaving]);

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
    <div ref={anchor} className="hw-menu-anchor">
      <button
        ref={trigger}
        type="button"
        className="hw-bell"
        aria-label={`${n} unacknowledged alert${n === 1 ? "" : "s"}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={trayId}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="hw-icon fa-solid fa-bell" aria-hidden="true" />
        {n > 0 && <span className="hw-bell__count" aria-hidden="true">{n}</span>}
      </button>

      <div ref={tray} id={trayId} className="hw-alert-tray" role="dialog" aria-label="Unacknowledged alerts" hidden={!open && !leaving} onKeyDown={onKeyDown}>
        <div className="hw-alert-tray__head">
          <h2 className="hw-alert-tray__title">Unacknowledged alerts{feed.data ? ` on Ward ${feed.data.ward}` : ""}</h2>
          <button ref={close} type="button" className="hw-close" aria-label="Close alerts" onClick={shut}>
            <span className="hw-icon fa-solid fa-xmark" aria-hidden="true" />
          </button>
        </div>
        {alerts.map((r, i) => (
          <div key={r.id} className="hw-alert-tray__item">
            {/* The line beside the tile writes the band, so the tile leaves its word out. */}
            <Score score={r.score} band={band(r)} small bandWord={false} />
            <div className="hw-alert-tray__who">
              <p className="hw-alert-tray__name">{r.name}</p>
              {/* No time: the ward does not say since when an alert has waited. */}
              <p className="hw-alert-tray__line">Bed {r.bed} &middot; {BAND_WORD[band(r)]}</p>
            </div>
            <button
              type="button"
              className="hw-button"
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
              className="hw-button hw-button--accent"
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
        {n === 0 && <p className="hw-alert-tray__empty">Nothing is waiting. New alerts appear here and on the patient&rsquo;s row.</p>}
        <p className="hw-alert-tray__foot">BioSentry does not replace a monitor alarm.</p>
      </div>
    </div>
  );
}
