/**
 * GeoCities mode — a purely cosmetic, opt-in 90s "personal homepage" reskin of
 * the entire UI (Comic Sans, tiled starfield, marquees, a hit counter, the
 * works). Toggled at build time via the VITE_GEOCITIES env var so it adds zero
 * runtime cost when it's off.
 *
 * Enable it by building with VITE_GEOCITIES=true (docker-compose: GEOCITIES_MODE=true).
 */
export const GEOCITIES_MODE =
  import.meta.env.VITE_GEOCITIES === "true" || import.meta.env.VITE_GEOCITIES === "1";
