import type { SessionRole } from "../types";

/** The roles that may ask the ward's own questions: one rule for the routes, the Research menu and the patient page's link. */
export const asksWard = (role: SessionRole | undefined): boolean => role === "md" || role === "charge_rn";
