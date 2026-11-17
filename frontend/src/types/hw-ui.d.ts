// The parts of the vendored hw-ui kit (vendor/hw-ui/src/js) this app calls. Alias "hw-ui" in vite.config.ts.
declare module "hw-ui" {
  interface Graph { destroy(): void }
  interface Common { title: string; subtitle?: string; unit?: string; hint?: string; level?: number; format?: (v: number) => string }
  export function trend(el: HTMLElement, config: Common & {
    x: string[]; xLabel?: string; max?: number;
    series: { name: string; values: (number | null)[]; dashed?: boolean }[];
    highlight?: string | number;
    /** A clinical y-range in place of zero. */
    range?: [number, number];
    /** Lines drawn dashed and named at their end; grey unless toned. */
    limits?: { at: number; label?: string; tone?: "urgent" | "emergency" }[];
    /** A shaded usual range, named (default "Usual lo–hi"). */
    band?: [number, number];
    bandLabel?: string;
    /** Only the stretch where this holds takes the highlight colour. */
    highlightWhere?: (v: number) => boolean;
  }): Graph & { update(config: unknown): void };
  export interface BarsConfig extends Common {
    items: { name: string; value: number; interval?: [number, number] }[];
    highlight?: number;
    /** Index of the item the readout compares the others with. */
    baseline?: number;
    max?: number;
    label?: (item: { name: string; value: number }) => string;
    /** Changes against a reference: a labelled zero line in the middle, losses left, gains right. */
    diverging?: boolean;
    zeroLabel?: string;
    sides?: [string, string];
    /** Names the per-item interval in the readout. */
    intervalLabel?: string;
  }
  export function bars(el: HTMLElement, config: BarsConfig): Graph;
  /** One measure over time; the picked column (default the latest) is read against the average. */
  export function columns(el: HTMLElement, config: Common & {
    x: string[]; values: number[]; highlight?: number; average?: boolean; averageLabel?: string; noun?: [string, string];
  }): Graph;
  export function range(el: HTMLElement, config: Common & {
    items: { label: string; value: number | null; unit?: string; usual: [number, number]; scale?: [number, number] }[];
  }): Graph;
  export function closeDialog(dialog: HTMLDialogElement | null, value?: string): Promise<void>;
  /** One toast in the page's live region; returns a function that dismisses it. `timeout` 0 keeps it until dismissed. */
  export function toast(input: string | {
    message: string; tone?: "success" | "danger" | "warning" | "info"; icon?: string; timeout?: number;
    action?: { label: string; onClick?: () => void };
  }): () => void;
  /** Plays the element's exit; resolves true when it finished, false when cancelLeave() interrupted it. */
  export function leave(el: Element | null): Promise<boolean>;
  export function cancelLeave(el: Element | null): void;
}
