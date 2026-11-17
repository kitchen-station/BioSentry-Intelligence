// The parts of the vendored hw-ui kit (vendor/hw-ui/src/js) this app calls. Alias "hw-ui" in vite.config.ts.
declare module "hw-ui" {
  interface Graph { destroy(): void }
  interface Common { title: string; subtitle?: string; unit?: string; hint?: string; level?: number; format?: (v: number) => string }
  export function trend(el: HTMLElement, config: Common & {
    x: string[]; xLabel?: string; max?: number;
    series: { name: string; values: (number | null)[]; dashed?: boolean }[];
    highlight?: string | number;
  }): Graph & { update(config: unknown): void };
  export function range(el: HTMLElement, config: Common & {
    items: { label: string; value: number | null; unit?: string; usual: [number, number]; scale?: [number, number] }[];
  }): Graph;
  export function closeDialog(dialog: HTMLDialogElement | null, value?: string): Promise<void>;
}
