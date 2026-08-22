// utils/checkLoad.ts
export function onWebsiteLoaded(callback: () => void) {
  if (document.readyState === "complete") {
    callback();
  } else {
    const handleLoad = () => {
      callback();
      window.removeEventListener("load", handleLoad);
    };
    window.addEventListener("load", handleLoad);
  }
}
