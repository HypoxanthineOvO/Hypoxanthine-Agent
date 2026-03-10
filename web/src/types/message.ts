export interface Message {
  text?: string | null;
  image?: string | null;
  file?: string | null;
  audio?: string | null;
  sender: string;
  timestamp?: string;
  session_id: string;
}

export type ConnectionStatus =
  | "connecting"
  | "connected"
  | "disconnected"
  | "error";
