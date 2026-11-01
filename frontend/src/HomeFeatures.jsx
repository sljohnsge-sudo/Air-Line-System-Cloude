// "Why travellers choose Tick and Book" — five cards in a sliding showcase on
// the home page. Three show at a time on desktop (two on tablets, one on
// phones); every few seconds the row slides along by one card and loops round
// seamlessly. Pauses while hovered, focused or touched; arrows, dots and
// swipe let people move it themselves. Reduced-motion users get no autoplay.
import React, { useEffect, useRef, useState } from 'react';
import * as Ico from './WizardIcons.jsx';

const SLIDE_EVERY_MS = 4500;

// Counts 0 → target every time its card lands in one of the visible boxes:
// when it comes into view (scrolling, or sliding into the carousel's visible
// row — hidden cards are clipped, so they count as out of view) and again
// after every slide while it stays visible (replayKey changes). Resets to 0
// when it leaves.
function CountUp({ target, suffix = '', replayKey }) {
  const ref = useRef(null);
  const [value, setValue] = useState(0);
  const visible = useRef(false);
  const raf = useRef(0);
  const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const run = () => {
    cancelAnimationFrame(raf.current);
    const start = performance.now(), dur = 1400;
    const tick = (now) => {
      const t = Math.min(1, (now - start) / dur);
      setValue(Math.round(target * (1 - Math.pow(1 - t, 3))));
      if (t < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  };
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    if (reduce || !('IntersectionObserver' in window)) { setValue(target); return undefined; }
    const io = new IntersectionObserver(([entry]) => {
      visible.current = entry.isIntersecting;
      if (entry.isIntersecting) run(); else { cancelAnimationFrame(raf.current); setValue(0); }
    }, { threshold: 0.9 });
    io.observe(el);
    return () => { io.disconnect(); cancelAnimationFrame(raf.current); };
  }, [target]);
  // landed in a new box: count again once the slide has settled
  useEffect(() => {
    if (reduce || replayKey === undefined) return undefined;
    const t = setTimeout(() => { if (visible.current) run(); }, 750);
    return () => clearTimeout(t);
  }, [replayKey]);
  return <span ref={ref}>{value}{suffix}</span>;
}

export default function HomeFeatures({ hotelsEnabled, packagesEnabled, onHotels, onTours, onHotelOffers, onFlights }) {
  const [index, setIndex] = useState(0);
  const cards = [
    {
      key: 'stays', icon: Ico.IconBed,
      stat: <><CountUp target={32} replayKey={index} /> Million+</>,
      title: 'Accommodation Choices Worldwide',
      body: 'From city hotels to beach resorts and holiday homes — find the right stay anywhere in the world through our global accommodation partners.',
      note: 'Figures subject to change.',
      cta: hotelsEnabled ? { label: 'Browse hotels', onClick: onHotels } : null,
    },
    {
      key: 'tours', icon: Ico.IconGlobe,
      title: 'International Tour Packages',
      body: "Hand-picked overseas holidays to the world's favourite destinations, with flights, stays and sightseeing planned for a memorable trip.",
      cta: packagesEnabled ? { label: 'View packages', onClick: onTours } : null,
    },
    {
      key: 'lk-hotels', icon: Ico.IconTag,
      title: 'Special Sri Lankan Hotel Offers',
      body: 'Exclusive promotions and great-value deals at selected hotels and resorts across Sri Lanka.',
      cta: packagesEnabled ? { label: 'See offers', onClick: onHotelOffers } : null,
    },
    {
      key: 'flights', icon: Ico.IconPlane,
      title: 'Global Flight Connectivity',
      body: 'Compare flights from airlines worldwide, with live fares and a fast, simple online booking.',
      points: ['Live prices, every time', 'Instant confirmation', 'E-ticket in your inbox'],
      cta: { label: 'Search flights', onClick: onFlights },
    },
    {
      key: 'pay', icon: Ico.IconCard,
      title: 'Easy & Secure Payments',
      body: 'Convenient, secure payment options for flight tickets, tour packages and travel services.',
      highlight: 'Easy payment plans with leading banks',
      note: 'Terms and conditions apply.',
    },
  ];
  const n = cards.length;
  const slides = [...cards, ...cards];          // second copy lets the loop wrap seamlessly

  const [animate, setAnimate] = useState(true);
  const [paused, setPaused] = useState(false);
  const touchX = useRef(null);
  const reduceMotion = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  useEffect(() => {
    if (paused || reduceMotion) return undefined;
    const t = setInterval(() => { setAnimate(true); setIndex(i => i + 1); }, SLIDE_EVERY_MS);
    return () => clearInterval(t);
  }, [paused, reduceMotion]);

  // After sliding onto the copies, jump back to the same cards in the first
  // set once the slide has finished. A timer, not transitionend, so it also
  // works with motion turned off and in background tabs.
  useEffect(() => {
    if (index < n) return undefined;
    const t = setTimeout(() => { setAnimate(false); setIndex(i => (i >= n ? i - n : i)); }, 750);
    return () => clearTimeout(t);
  }, [index, n]);
  useEffect(() => {
    if (!animate) { const r = requestAnimationFrame(() => setAnimate(true)); return () => cancelAnimationFrame(r); }
    return undefined;
  }, [animate]);

  const go = (delta) => {
    if (delta < 0 && index <= 0) {
      // from the first card: jump (unseen) to its copy, then slide back one
      setAnimate(false); setIndex(n);
      requestAnimationFrame(() => requestAnimationFrame(() => { setAnimate(true); setIndex(n - 1); }));
      return;
    }
    setAnimate(true);
    setIndex(i => i + delta);
  };
  const active = ((index % n) + n) % n;

  return (
    <section className="features-section hf-section">
      <div className="section-header">
        <span className="section-label">Why travellers choose Tick and Book</span>
        <h2 className="section-heading">Everything for your journey, in one place</h2>
        <p className="hf-subtitle">Flights, stays, holidays and easy payments — planned and booked with confidence.</p>
      </div>

      <div
        className="hf-carousel"
        onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}
        onFocus={() => setPaused(true)} onBlur={() => setPaused(false)}
        onTouchStart={e => { setPaused(true); touchX.current = e.touches[0].clientX; }}
        onTouchEnd={e => {
          const dx = e.changedTouches[0].clientX - (touchX.current ?? e.changedTouches[0].clientX);
          if (Math.abs(dx) > 40) go(dx < 0 ? 1 : -1);
          setPaused(false);
        }}
        aria-roledescription="carousel"
      >
        <button type="button" className="hf-arrow hf-prev" aria-label="Previous" onClick={() => go(-1)}><Ico.IconChevronLeft size={20} /></button>
        <div className="hf-viewport">
          <div
            className="hf-track"
            style={{ '--i': index, transition: animate ? undefined : 'none' }}
          >
            {slides.map((c, k) => {
              const Icon = c.icon;
              return (
                <article key={`${c.key}-${k}`} className={`feature-card hf-card hf-${c.key}`} aria-hidden={k >= n ? 'true' : undefined}>
                  <div className="feature-icon-box hf-icon"><Icon size={22} /></div>
                  {c.stat && <div className="hf-stat">{c.stat}</div>}
                  <h4>{c.title}</h4>
                  <p>{c.body}</p>
                  {c.points && (
                    <ul className="hf-points">
                      {c.points.map(pt => <li key={pt}><span aria-hidden="true">✓</span>{pt}</li>)}
                    </ul>
                  )}
                  {c.highlight && <div className="hf-highlight"><Ico.IconCard size={16} />{c.highlight}</div>}
                  <div className="hf-foot">
                    {c.note && <span className="hf-note">{c.note}</span>}
                    {c.cta && (
                      <button type="button" className="hf-cta" tabIndex={k >= n ? -1 : undefined} onClick={c.cta.onClick}>
                        {c.cta.label}<Ico.IconChevronRight size={16} />
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </div>
        <button type="button" className="hf-arrow hf-next" aria-label="Next" onClick={() => go(1)}><Ico.IconChevronRight size={20} /></button>
      </div>

      <div className="hf-dots" role="tablist" aria-label="Choose a card">
        {cards.map((c, k) => (
          <button key={c.key} type="button" role="tab" aria-selected={k === active} aria-label={c.title}
                  className={`hf-dot${k === active ? ' active' : ''}`}
                  onClick={() => { setAnimate(true); setIndex(k); }} />
        ))}
      </div>
    </section>
  );
}
