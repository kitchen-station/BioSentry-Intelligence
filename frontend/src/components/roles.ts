import { createContext, useContext } from "react";
import type { SessionRole } from "../types";

/** The roles that may ask the ward's own questions: one rule for the routes, the Research menu and the assistant. */
export const asksWard = (role: SessionRole | undefined): boolean => role === "md" || role === "charge_rn";

/** The patient page's button fires this, with itself as the detail; the assistant's panel opens on it. */
export const OPEN_CHAT = "biosentry:open-chat";

/** Opens the assistant's panel from `from`, which gets the focus back when the panel closes. */
export const openAssistant = (from: HTMLElement): void => void window.dispatchEvent(new CustomEvent(OPEN_CHAT, { detail: from }));

/**
 * The assistant as the top bar sees it: null where it cannot be opened, else whether its panel is open and
 * how to open or close it. The panel (ChatBubble, rendered once by App) provides it; this module holds no api
 * call, so the lab pages' bar imports none.
 */
export interface Assistant {
  readonly open: boolean;
  readonly toggle: (from: HTMLElement) => void;
}

export const AssistantContext = createContext<Assistant | null>(null);

export const useAssistant = (): Assistant | null => useContext(AssistantContext);
