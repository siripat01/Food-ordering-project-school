"use client";

import { useEffect, useRef } from "react";

const IMPRESSION_DWELL_MS = 750;
const IMPRESSION_VISIBILITY_THRESHOLD = 0.5;

export function useRecommendationImpression(
  onVisible: () => void,
  enabled = true,
) {
  const elementRef = useRef<HTMLAnchorElement>(null);
  const onVisibleRef = useRef(onVisible);

  useEffect(() => {
    onVisibleRef.current = onVisible;
  }, [onVisible]);

  useEffect(() => {
    if (!enabled) return;
    const element = elementRef.current;
    if (!element || typeof IntersectionObserver === "undefined") return;

    let dwellTimer: ReturnType<typeof setTimeout> | undefined;
    let isVisible = false;
    let recorded = false;

    const cancelDwell = () => {
      if (dwellTimer !== undefined) {
        clearTimeout(dwellTimer);
        dwellTimer = undefined;
      }
    };

    const scheduleDwell = () => {
      if (
        recorded ||
        dwellTimer !== undefined ||
        !isVisible ||
        document.visibilityState !== "visible"
      ) {
        return;
      }

      dwellTimer = setTimeout(() => {
        dwellTimer = undefined;
        if (
          recorded ||
          !isVisible ||
          document.visibilityState !== "visible"
        ) {
          return;
        }
        recorded = true;
        onVisibleRef.current();
      }, IMPRESSION_DWELL_MS);
    };

    const observer = new IntersectionObserver(
      ([entry]) => {
        isVisible =
          entry.isIntersecting &&
          entry.intersectionRatio >= IMPRESSION_VISIBILITY_THRESHOLD;
        if (isVisible) scheduleDwell();
        else cancelDwell();
      },
      { threshold: IMPRESSION_VISIBILITY_THRESHOLD },
    );

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") scheduleDwell();
      else cancelDwell();
    };

    observer.observe(element);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      cancelDwell();
      observer.disconnect();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [enabled]);

  return elementRef;
}
