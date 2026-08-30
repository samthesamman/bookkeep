import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

/** window.scrollY remembered per history entry (location.key). */
const scrollPositions = new Map<string, number>();
/** True while we're programmatically replaying a saved position. */
let isRestoring = false;

/** Scroll the window now, ignoring `html { scroll-behavior: smooth }`. */
function jumpTo(top: number) {
  const root = document.documentElement;
  const previous = root.style.scrollBehavior;
  root.style.scrollBehavior = 'auto';
  window.scrollTo(0, top);
  root.style.scrollBehavior = previous;
}

/**
 * Manual scroll restoration for the SPA.
 *
 * The scroll offset for the active history entry is tracked live on every
 * scroll event — it can't be read at navigation time because swapping the
 * route's content shrinks the document and the browser clamps `scrollY`
 * before any effect runs.
 *
 * On a location change we look for an offset saved against the new entry's
 * `key`: found (back / forward) → replay it, retrying for ~2s while the lazy
 * chunk mounts and cached queries repaint; not found (a fresh push) → top.
 * The browser's own restoration is disabled — it gives up long before an async
 * list page has its rows.
 */
export function ScrollRestoration() {
  const { key } = useLocation();

  useEffect(() => {
    if ('scrollRestoration' in window.history) {
      window.history.scrollRestoration = 'manual';
    }
  }, []);

  // Track the live scroll offset for this entry.
  useEffect(() => {
    const onScroll = () => {
      if (!isRestoring) scrollPositions.set(key, window.scrollY);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [key]);

  // Restore (or reset) for the incoming entry. Declared after the tracker so
  // that on a navigation React tears down the tracker's listener before this
  // runs, and the outgoing offset is already saved.
  useEffect(() => {
    const saved = scrollPositions.get(key);

    if (saved == null) {
      jumpTo(0);
      return;
    }

    isRestoring = true;
    let rafId = 0;
    const deadline = performance.now() + 2000;

    const step = () => {
      jumpTo(saved);
      if (Math.abs(window.scrollY - saved) <= 1 || performance.now() > deadline) {
        isRestoring = false;
        scrollPositions.set(key, window.scrollY);
        return;
      }
      rafId = requestAnimationFrame(step);
    };
    step();

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      isRestoring = false;
    };
  }, [key]);

  return null;
}
