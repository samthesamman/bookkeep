import { useEffect, useState } from 'react';

/** A fake-but-stable "you are visitor #N" counter, persisted per browser. */
function useHitCount(): string {
  const [count, setCount] = useState(1337);

  useEffect(() => {
    const KEY = 'bookkeep-geocities-hits';
    let n = 0;
    try {
      n = parseInt(localStorage.getItem(KEY) || '', 10) || 0;
    } catch {
      /* private mode / disabled storage — just show the seed */
    }
    if (!n) {
      // Seed somewhere fun the first time around.
      n = 24000 + Math.floor(Math.random() * 6000);
    }
    n += 1;
    try {
      localStorage.setItem(KEY, String(n));
    } catch {
      /* ignore */
    }
    setCount(n);
  }, []);

  return String(count).padStart(6, '0');
}

/**
 * The 90s "personal homepage" furniture: a scrolling welcome marquee, an
 * under-construction sign, a hit counter and a webring-style footer. Rendered
 * only when GeoCities mode is on (see AppLayout).
 */
export function GeocitiesBanner() {
  const hits = useHitCount();

  return (
    <div className="geo-panel">
      <div className="geo-marquee" role="marquee">
        <span>
          &#128218;&#127760; WELCOME TO BOOKSTORE &mdash; MY LIL' CORNER OF THE WEB &#127760;&#128218;
          &nbsp; sign my guestbook! &nbsp; &#9733; &nbsp; new books added ALL the time &nbsp;
          &#9733; &nbsp; best viewed in Netscape Navigator 4.0 &nbsp; &#9733; &nbsp; thanks 4 visiting!!
        </span>
      </div>

      <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: 'center', alignItems: 'center' }}>
        <span className="geo-construction">
          <span>&#128679; UNDER CONSTRUCTION &#128679;</span>
        </span>
        <span>
          You are visitor&nbsp;
          <span className="geo-counter">{hits}</span>
        </span>
        <span className="geo-blink" style={{ color: '#ff2222', fontWeight: 'bold' }}>
          &#11088; NEW! &#11088;
        </span>
      </div>
    </div>
  );
}

export function GeocitiesFooter() {
  return (
    <div className="geo-footer">
      <p>
        This page is powered by <b>Bookstore</b> and a lot of instant coffee &#9749;
      </p>
      <p>
        Best viewed at 800&times;600 &nbsp;|&nbsp; Made with Notepad &nbsp;|&nbsp;
        <span className="geo-blink"> Y2K ready! </span>
      </p>
      <p>
        [ <a href="#top">Home</a> ]
        [ <a href="#top">Guestbook</a> ]
        [ <a href="#top">Webring</a> ]
        [ <a href="#top">E-mail me</a> ]
      </p>
      <p>&copy; {new Date().getFullYear()} &mdash; all rights reserved, do not steal my HTML</p>
    </div>
  );
}
