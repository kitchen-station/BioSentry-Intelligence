import type { SessionRole } from "../types";

/** The roles that may ask the ward's own questions: one rule for the routes and for the chat bubble. */
export const asksWard = (role: SessionRole | undefined): boolean => role === "md" || role === "charge_rn";

/** The patient page's button fires this; the chat bubble opens on it. */
export const OPEN_CHAT = "biosentry:open-chat";
