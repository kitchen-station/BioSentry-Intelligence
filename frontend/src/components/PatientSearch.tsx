import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import type { WardResponse } from "../types";
import { band } from "./risk";
import { BAND_TAG, BAND_WORD } from "./Score";

const MAX = 6;

const meta = (r: { age: number | null; sex: string | null; bed: string }) => `${r.age ?? "--"}${r.sex ? ` ${r.sex}` : ""} · Bed ${r.bed}`;

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
        ...ward.ranked.filter(hit).map((r) => ({
          id: r.id, name: r.name, meta: meta(r), rank: `#${r.rank}`, tag: `${r.score} ${BAND_WORD[band(r)]}`, tagClass: `${BAND_TAG[band(r)]} hw-tag--round`,
        })),
        ...ward.abstained.filter(hit).map((r) => ({
          id: r.id, name: r.name, meta: meta(r), rank: "", tag: "NOT RANKED", tagClass: "hw-tag hw-tag--round hw-tag--dashed",
        })),
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
    <div className="hw-search">
      <label className="hw-input">
        <span className="hw-icon hw-input__icon fa-solid fa-magnifying-glass" aria-hidden="true" />
        <input
          ref={input}
          type="search"
          className="hw-input__control"
          role="combobox"
          aria-label="Find a patient by name or bed"
          aria-autocomplete="list"
          aria-expanded={expanded && results.length > 0}
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
        <kbd className="hw-search__key" aria-hidden="true">/</kbd>
      </label>
      {expanded && results.length > 0 && (
        // A press on a result must not blur the field first, or the result is gone before the click lands.
        <ul id={listId} className="hw-search__results" role="listbox" aria-label="Patients in rank order" onMouseDown={(e) => e.preventDefault()}>
          <li className="hw-search__caption" aria-hidden="true">In rank order</li>
          {results.map((r, i) => (
            <li key={r.id} id={optionId(i)} role="option" aria-selected={i === active} onClick={() => open(r.id)}>
              <span className="hw-search__rank">{r.rank}</span>
              <span className="hw-search__name">{r.name}</span>
              <span className="hw-search__bed">{r.meta}</span>
              <span className={r.tagClass}>{r.tag}</span>
            </li>
          ))}
        </ul>
      )}
      {expanded && results.length === 0 && (
        <p className="hw-search__empty" role="status">No patient on Ward {ward.ward} matches. Try a surname or a bed.</p>
      )}
    </div>
  );
}
