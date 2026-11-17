import { Flip } from "./Flip";
import type { Band } from "./risk";

export const BAND_WORD: Record<Band, string> = { high: "High", medium: "Medium", "low-medium": "Low-medium", low: "Low" };

/** The NEWS2 tile (kit hw-score): the number and its band word, always together. Low-medium wears the medium colour. */
export function Score({ score, band, rising = false, small = false }: { score: number; band: Band; rising?: boolean; small?: boolean }) {
  const word = BAND_WORD[band];
  return (
    <div
      className={small ? "hw-score hw-score--sm" : "hw-score"}
      data-band={band === "low-medium" ? "medium" : band}
      role="img"
      aria-label={`NEWS2 ${score}, ${word}${rising ? ", rising" : ""}`}
    >
      <span className="hw-score__value"><Flip>{score}</Flip></span>
      <span className="hw-score__band">
        {word}
        {rising && <span className="hw-icon fa-solid fa-arrow-up" aria-hidden="true" />}
      </span>
    </div>
  );
}
