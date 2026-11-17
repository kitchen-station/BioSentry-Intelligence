import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import type { WardResponse } from "../types";
import { band } from "./risk";
import { BAND_WORD } from "./Score";
import styles from "./PatientSearch.module.css";

const MAX = 6;

/**
 * Find a patient by name or bed (kit hw-search). Results in rank order, NOT RANKED last, at most
 * six. `/` focuses the field from anywhere outside a field; Up and Down move the active result,
 * Enter opens it, Esc clears the field and then leaves it.
 */
export function PatientSearch({ ward }: { ward: WardResponse }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [focused, setFocused] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const navigate = useNavigate();

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement;
      if (t.isContentEditable || t.closest?.("input, textarea, select")) return;
      e.preventDefault();
      input.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const q = query.trim().toLowerCase();
  const hit = (r: { name: string; bed: string }) => r.name.toLowerCase().includes(q) || r.bed.toLowerCase().includes(q);
  const results = q === ""
    ? []
    : [
        ...ward.ranked.filter(hit).map((r) => ({ id: r.id, name: r.name, bed: r.bed, rank: `#${r.rank}`, tag: `${r.score} ${BAND_WORD[band(r)]}` })),
        ...ward.abstained.filter(hit).map((r) => ({ id: r.id, name: r.name, bed: r.bed, rank: "", tag: null })),
      ].slice(0, MAX);
  const expanded = focused && q !== "";
  const optionId = (i: number) => `${listId}-${i}`;

  const open = (id: string) => {
    setQuery("");
    input.current?.blur();
    navigate(`/patient/${id}`);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, Math.min(results.length - 1, i + (e.key === "ArrowDown" ? 1 : -1))));
    } else if (e.key === "Enter" && results[active]) {
      e.preventDefault();
      open(results[active].id);
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (query) setQuery("");
      else input.current?.blur();
    }
  };

  return (
    <div className={`hw-search ${styles.search}`}>
      <label className="hw-input">
        <span className="hw-icon fa-solid fa-magnifying-glass" aria-hidden="true" />
        <input
          ref={input}
          type="search"
          className="hw-input__control"
          role="combobox"
          aria-label="Find a patient by name or bed"
          aria-autocomplete="list"
          aria-expanded={expanded}
          aria-controls={listId}
          aria-activedescendant={expanded && results[active] ? optionId(active) : undefined}
          placeholder="Find a patient"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        />
        <kbd aria-hidden="true">/</kbd>
      </label>
      {expanded && (
        // A press on a result must not blur the field first, or the result is gone before the click lands.
        <ul id={listId} className="hw-search__results" role="listbox" aria-label="Patients, in rank order" onMouseDown={(e) => e.preventDefault()}>
          {results.map((r, i) => (
            <li key={r.id} id={optionId(i)} role="option" aria-selected={i === active} onClick={() => open(r.id)}>
              <span className={styles.rank}>{r.rank}</span>
              <span className={styles.name}>{r.name}</span>
              <span className={styles.bed}>Bed {r.bed}</span>
              {r.tag !== null ? <span className="hw-tag">{r.tag}</span> : <span className="hw-tag hw-tag--dashed">NOT RANKED</span>}
            </li>
          ))}
          {results.length === 0 && (
            <li role="option" aria-selected={false} aria-disabled="true" className={styles.none}>
              No patient on Ward {ward.ward} matches. Try a surname or a bed.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
