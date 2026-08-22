"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiBaseUrl } from "../libs/axios";

export type StreamConnectionState =
  | "connecting"
  | "live"
  | "reconnecting"
  | "disconnected";

type StaffOrderStreamOptions = {
  enabled: boolean;
  onSnapshot: (payload: unknown) => void;
  onOrderUpdated: (payload: unknown) => void;
};

function getStreamUrl() {
  const baseUrl = new URL(apiBaseUrl, window.location.origin);
  return `${baseUrl.toString().replace(/\/$/, "")}/staff/orders/stream`;
}

export function useStaffOrderStream({
  enabled,
  onSnapshot,
  onOrderUpdated,
}: StaffOrderStreamOptions) {
  const callbacksRef = useRef({ onSnapshot, onOrderUpdated });
  const [connectionState, setConnectionState] =
    useState<StreamConnectionState>("disconnected");
  const [lastEventAt, setLastEventAt] = useState<Date | null>(null);
  const [connectionKey, setConnectionKey] = useState(0);

  useEffect(() => {
    callbacksRef.current = { onSnapshot, onOrderUpdated };
  }, [onOrderUpdated, onSnapshot]);

  useEffect(() => {
    if (!enabled) return;

    setConnectionState("connecting");
    const eventSource = new EventSource(getStreamUrl(), {
      withCredentials: true,
    });

    const readEvent = (
      event: Event,
      callback: (payload: unknown) => void,
    ) => {
      if (!(event instanceof MessageEvent)) return;

      try {
        callback(JSON.parse(event.data));
        setLastEventAt(new Date());
      } catch {
        // Ignore malformed events while keeping the last valid queue on screen.
      }
    };

    eventSource.onopen = () => setConnectionState("live");
    eventSource.onerror = () => setConnectionState("reconnecting");
    eventSource.addEventListener("snapshot", (event) =>
      readEvent(event, callbacksRef.current.onSnapshot),
    );
    eventSource.addEventListener("order.updated", (event) =>
      readEvent(event, callbacksRef.current.onOrderUpdated),
    );
    eventSource.addEventListener("heartbeat", () => {
      setLastEventAt(new Date());
    });

    return () => eventSource.close();
  }, [connectionKey, enabled]);

  const reconnect = useCallback(() => {
    if (enabled) setConnectionKey((current) => current + 1);
  }, [enabled]);

  return { connectionState, lastEventAt, reconnect };
}
