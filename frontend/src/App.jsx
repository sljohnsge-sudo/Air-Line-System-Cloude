import React, { useState, useEffect, useRef } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import gsLogo from './assets/george_steuart_logo.png';

const API_BASE = 'http://localhost:8030/api';

const fetchWithRetry = async (url, options = {}, retries = 3, delay = 1000) => {
  for (let i = 0; i < retries; i++) {
    try {
      return await fetch(url, options);
    } catch (err) {
      const isNetworkError = 
        err instanceof TypeError || 
        err.name === 'TypeError' || 
        err.message?.includes('fetch') || 
        err.message?.includes('NetworkError') ||
        err.message?.includes('Network Error') ||
        err.message?.includes('Failed to fetch') ||
        err.message?.includes('Failed to connect') ||
        err.message?.includes('NetworkError when attempting to fetch resource');
                             
      if (isNetworkError && i < retries - 1) {
        console.warn(`Fetch to ${url} failed with network error: ${err.message}. Retrying in ${delay}ms... (Attempt ${i + 1}/${retries})`);
        await new Promise(resolve => setTimeout(resolve, delay));
        continue;
      }
      throw err;
    }
  }
};

// ── Popular IATA airport codes ──────────────────────────────────────────────
const AIRPORTS = [
  { code: 'CMB', name: 'Colombo (CMB)' },
  { code: 'DXB', name: 'Dubai (DXB)' },
  { code: 'LHR', name: 'London Heathrow (LHR)' },
  { code: 'SIN', name: 'Singapore (SIN)' },
  { code: 'JFK', name: 'New York JFK (JFK)' },
  { code: 'LAX', name: 'Los Angeles (LAX)' },
  { code: 'ORD', name: 'Chicago O\'Hare (ORD)' },
  { code: 'SFO', name: 'San Francisco (SFO)' },
  { code: 'ATL', name: 'Atlanta (ATL)' },
  { code: 'BOM', name: 'Mumbai (BOM)' },
  { code: 'DEL', name: 'Delhi (DEL)' },
  { code: 'HND', name: 'Tokyo Haneda (HND)' },
  { code: 'SYD', name: 'Sydney (SYD)' },
  { code: 'CDG', name: 'Paris CDG (CDG)' },
  { code: 'FRA', name: 'Frankfurt (FRA)' },
  { code: 'AMS', name: 'Amsterdam (AMS)' },
  { code: 'IST', name: 'Istanbul (IST)' },
  { code: 'DOH', name: 'Doha (DOH)' },
  { code: 'KUL', name: 'Kuala Lumpur (KUL)' },
  { code: 'BKK', name: 'Bangkok (BKK)' },
  { code: 'NRT', name: 'Tokyo Narita (NRT)' },
  { code: 'ICN', name: 'Seoul Incheon (ICN)' },
  { code: 'MIA', name: 'Miami (MIA)' },
  { code: 'MCO', name: 'Orlando (MCO)' },
];

// ── Trending Destinations (display only) ───────────────────────────────────
const trendingDestinations = [
  { city: 'Dubai', country: 'UAE', code: 'DXB', image: 'https://images.unsplash.com/photo-1512453979798-5ea266f8880c?auto=format&fit=crop&w=500&q=80', tag: 'Golden City' },
  { city: 'Singapore', country: 'Southeast Asia', code: 'SIN', image: 'https://images.unsplash.com/photo-1525625293386-3f8f99389edd?auto=format&fit=crop&w=500&q=80', tag: 'Garden City' },
  { city: 'London', country: 'United Kingdom', code: 'LHR', image: 'https://images.unsplash.com/photo-1513635269975-59663e0ca1ad?auto=format&fit=crop&w=500&q=80', tag: 'Royal Heritage' },
  { city: 'New York', country: 'United States', code: 'JFK', image: 'https://images.unsplash.com/photo-1496442226666-8d4d0e62e6e9?auto=format&fit=crop&w=500&q=80', tag: 'Metro Hub' },
];

// ── Tomorrow's date helper ─────────────────────────────────────────────────
const getTomorrow = () => {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().split('T')[0];
};

// ── Dynamic Airport Search Autocomplete ─────────────────────────────────────
function AirportSearchSelect({ value, onChange, label, placeholder }) {
  const [searchTerm, setSearchTerm] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [displayName, setDisplayName] = useState('');

  // Fetch airport info on value change to update label
  useEffect(() => {
    if (value) {
      fetchWithRetry(`${API_BASE}/reference/airports?q=${encodeURIComponent(value)}`)
        .then(res => res.json())
        .then(data => {
          const match = data.airports?.find(a => a.iata === value.toUpperCase());
          if (match) {
            const display = `${match.city} (${match.iata})`;
            setDisplayName(`${display} - ${match.name}`);
            setSearchTerm(display);
          } else {
            setDisplayName(value);
            setSearchTerm(value);
          }
        })
        .catch(() => {
          setDisplayName(value);
          setSearchTerm(value);
        });
    } else {
      setDisplayName('');
      setSearchTerm('');
    }
  }, [value]);

  const handleInputFocus = () => {
    setShowDropdown(true);
    fetchSuggestions(searchTerm);
  };

  const handleInputChange = (e) => {
    const query = e.target.value;
    setSearchTerm(query);
    fetchSuggestions(query);
  };

  const fetchSuggestions = (query) => {
    fetchWithRetry(`${API_BASE}/reference/airports?q=${encodeURIComponent(query)}`)
      .then(res => res.json())
      .then(data => {
        // If query is empty, it returns the top default major airports
        setSuggestions(data.airports || []);
      })
      .catch(() => {
        setSuggestions([]);
      });
  };

  const handleSelect = (airport) => {
    onChange(airport.iata);
    setSearchTerm(`${airport.city} (${airport.iata})`);
    setDisplayName(`${airport.city} (${airport.iata}) - ${airport.name}`);
    setShowDropdown(false);
  };

  // Close suggestions list on clicking outside the container
  useEffect(() => {
    const handleOutsideClick = (e) => {
      if (!e.target.closest(`.autocomplete-${label?.replace(/[^a-zA-Z0-9]/g, '')}`)) {
        setShowDropdown(false);
        // Reset raw search input to the formatted displayName code
        if (value) {
          const parts = displayName.split(' - ');
          setSearchTerm(parts[0] || value);
        } else {
          setSearchTerm('');
        }
      }
    };
    document.addEventListener('mousedown', handleOutsideClick);
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, [displayName, value, label]);

  return (
    <div className={`autocomplete-container autocomplete-${label?.replace(/[^a-zA-Z0-9]/g, '')} form-group flex-1`}>
      {label && <label className="form-label">{label}</label>}
      <input
        type="text"
        className="form-input"
        placeholder={placeholder || 'Search city or airport...'}
        value={searchTerm}
        onChange={handleInputChange}
        onFocus={handleInputFocus}
        style={{ width: '100%' }}
      />
      {showDropdown && suggestions.length > 0 && (
        <div className="autocomplete-dropdown">
          {suggestions.map((airport) => (
            <div
              key={airport.iata}
              className={`autocomplete-item ${value === airport.iata ? 'active' : ''}`}
              onClick={() => handleSelect(airport)}
            >
              <div className="autocomplete-details">
                <span className="autocomplete-name">{airport.city} ({airport.iata})</span>
                <span className="autocomplete-location">{airport.name}, {airport.country}</span>
              </div>
              <span className="autocomplete-code">{airport.iata}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Clean Passenger Name Helper ──────────────────────────────────────────────
const cleanPassengerName = (name) => {
  if (!name) return '';
  const parts = name.trim().toUpperCase().split(/\s+/);
  const titles = ['MR', 'MRS', 'MS', 'MSTR', 'DR', 'PROF', 'MISS'];
  
  let foundTitle = '';
  let cleanParts = [];
  
  for (let part of parts) {
    let matchedTitle = '';
    for (const title of titles) {
      if (part === title) {
        matchedTitle = title;
        break;
      }
      if (part.length > title.length && part.endsWith(title)) {
        matchedTitle = title;
        part = part.slice(0, -title.length);
        break;
      }
    }
    
    if (matchedTitle) {
      foundTitle = matchedTitle;
      if (part !== matchedTitle && part.trim()) {
        cleanParts.push(part.trim());
      }
    } else {
      cleanParts.push(part);
    }
  }
  
  if (foundTitle) {
    return `${foundTitle} ${cleanParts.join(' ')}`;
  }
  return cleanParts.join(' ');
};

// ── Parse API Error Helper ──────────────────────────────────────────────────
const parseApiError = (data, fallback) => {
  if (!data) return fallback;
  if (data.detail) {
    if (Array.isArray(data.detail)) {
      return data.detail
        .map(d => {
          if (d && typeof d === 'object') {
            const locStr = d.loc ? `Field ${d.loc.slice(1).join('.')}` : '';
            return `${locStr ? locStr + ': ' : ''}${d.msg || JSON.stringify(d)}`;
          }
          return String(d);
        })
        .join('; ');
    }
    if (typeof data.detail === 'object') {
      return data.detail.message || data.detail.msg || JSON.stringify(data.detail);
    }
    return String(data.detail);
  }
  return data.message || data.error || fallback;
};

// ── Handle API Response Helper ──────────────────────────────────────────────
const handleApiResponse = async (res, defaultError) => {
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw new Error(`${defaultError}: Invalid server response`);
  }
  if (!res.ok) {
    const parsedError = parseApiError(data, defaultError);
    throw new Error(parsedError);
  }
  return data;
};

// ── Itinerary Row: one leg's departure/arrival/duration strip ──────────────
// Used once for a one-way result, and twice (Outbound/Return) for a merged
// round-trip result card.
function ItineraryRow({ leg, label }) {
  return (
    <div className="rc-itinerary">
      {label && (
        <div style={{
          position: 'absolute', top: '-0.6rem', left: '0.75rem',
          background: 'var(--gs-crimson)', color: 'white', fontSize: '0.6rem',
          fontWeight: '800', letterSpacing: '0.04em', textTransform: 'uppercase',
          borderRadius: '3px', padding: '0.1rem 0.45rem'
        }}>
          {label}
        </div>
      )}
      <div className="rc-endpoint">
        <div className="rc-time">{leg.departure_time?.split('T')[1]?.slice(0,5) || leg.departure_time?.split(' ')[1]?.slice(0,5) || '--:--'}</div>
        <div className="rc-airport">{leg.departure_airport}</div>
        <div className="rc-date">{leg.departure_time?.split('T')[0] || leg.departure_time?.split(' ')[0] || ''}</div>
      </div>
      <div className="rc-path">
        <div className="rc-path-line"></div>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="var(--gs-crimson)"><path d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L14 19v-5.5L21 16z"/></svg>
        <div className="rc-path-line"></div>
        {leg.duration && <div className="rc-duration">{leg.duration.replace('PT','').replace('H','h ').replace('M','m')}</div>}
        {leg.segments && leg.segments.length > 1 && (
          <div style={{ fontSize: '0.65rem', color: '#b45309', fontWeight: '700', marginTop: '3px' }}>
            via {leg.segments.slice(0, -1).map(s => s.arrival_airport).join(', ')}
          </div>
        )}
      </div>
      <div className="rc-endpoint rc-endpoint-right">
        <div className="rc-time">{leg.arrival_time?.split('T')[1]?.slice(0,5) || leg.arrival_time?.split(' ')[1]?.slice(0,5) || '--:--'}</div>
        <div className="rc-airport">{leg.arrival_airport}</div>
        <div className="rc-date">{leg.arrival_time?.split('T')[0] || leg.arrival_time?.split(' ')[0] || ''}</div>
      </div>
    </div>
  );
}

// ── Segment List: connecting-flight breakdown for one leg (used in review) ──
function SegmentList({ segments }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
      {segments.map((seg, sIdx) => (
        <div key={sIdx} style={{ fontSize: '0.8rem', borderBottom: sIdx < segments.length - 1 ? '1px dashed #cbd5e1' : 'none', paddingBottom: '0.5rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: '700' }}>
            <span>{seg.departure_airport} → {seg.arrival_airport}</span>
            <span style={{ color: 'var(--gs-crimson)' }}>{seg.duration?.replace('PT','').replace('H','h ').replace('M','m')}</span>
          </div>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '2px', display: 'flex', gap: '1rem' }}>
            <span>{seg.carrier_name} • {seg.flight_number}</span>
            <span>Dep: {seg.departure_time?.split('T')[1]?.slice(0,5)} | Arr: {seg.arrival_time?.split('T')[1]?.slice(0,5)}</span>
          </div>
          {seg.layover_minutes !== undefined && (
            <div style={{ fontSize: '0.72rem', color: '#b45309', background: '#fffbeb', padding: '0.2rem 0.5rem', borderRadius: '4px', marginTop: '5px', fontWeight: '700' }}>
              ⏱️ Connection Layover: {Math.floor(seg.layover_minutes / 60)}h {seg.layover_minutes % 60}m at {seg.arrival_airport}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Segment Timeline: dot/line visual breakdown for one leg (results card) ──
function SegmentTimeline({ segments }) {
  return (
    <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', gap: '0.85rem', paddingLeft: '0.75rem', margin: '0.5rem 0' }}>
      {/* Visual Timeline Line */}
      <div style={{ position: 'absolute', left: '11px', top: '10px', bottom: '10px', width: '2px', background: '#cbd5e1', zIndex: 1 }}></div>

      {segments.map((seg, sIdx) => (
        <React.Fragment key={sIdx}>
          {/* Segment Block */}
          <div style={{ display: 'flex', gap: '1rem', position: 'relative', zIndex: 2, alignItems: 'flex-start' }}>
            {/* Point marker */}
            <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--gs-crimson)', border: '4px solid white', boxShadow: '0 0 0 1px #cbd5e1', marginTop: '4px', flexShrink: 0 }}></div>

            {/* Segment Card */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                <div>
                  <strong style={{ fontSize: '0.85rem', color: '#1e293b' }}>
                    {seg.departure_airport} → {seg.arrival_airport}
                  </strong>
                  <span style={{ fontSize: '0.75rem', color: '#64748b', marginLeft: '0.5rem', fontWeight: '500' }}>
                    ({seg.carrier_name} • {seg.flight_number})
                  </span>
                </div>
                <div style={{ fontSize: '0.78rem', fontWeight: '700', color: 'var(--gs-crimson)' }}>
                  {seg.duration?.replace('PT','').replace('H','h ').replace('M','m')}
                </div>
              </div>

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem 1.25rem', marginTop: '0.2rem', fontSize: '0.75rem', color: '#475569' }}>
                <div>
                  <span style={{ color: '#94a3b8' }}>Depart: </span>
                  <strong>{seg.departure_time?.split('T')[1]?.slice(0,5) || seg.departure_time?.split(' ')[1]?.slice(0,5)}</strong> on {seg.departure_time?.split('T')[0] || seg.departure_time?.split(' ')[0]}
                </div>
                <div>
                  <span style={{ color: '#94a3b8' }}>Arrive: </span>
                  <strong>{seg.arrival_time?.split('T')[1]?.slice(0,5) || seg.arrival_time?.split(' ')[1]?.slice(0,5)}</strong> on {seg.arrival_time?.split('T')[0] || seg.arrival_time?.split(' ')[0]}
                </div>
                {seg.aircraft_type && (
                  <div>
                    <span style={{ color: '#94a3b8' }}>Aircraft: </span>
                    <strong>{seg.aircraft_type}</strong>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Layover Indicator */}
          {seg.layover_minutes !== undefined && (
            <div style={{ display: 'flex', gap: '1rem', position: 'relative', zIndex: 2, alignItems: 'center', margin: '0.15rem 0' }}>
              <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#d97706', border: '4px solid white', boxShadow: '0 0 0 1px #f59e0b', flexShrink: 0 }}></div>
              <div style={{ flex: 1, background: '#fffbeb', border: '1px solid #fef3c7', borderRadius: '6px', padding: '0.4rem 0.75rem', fontSize: '0.72rem', color: '#b45309', fontWeight: '700', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                <span>⏱️</span>
                <span>Connection Layover: {Math.floor(seg.layover_minutes / 60)}h {seg.layover_minutes % 60}m at {seg.arrival_airport}</span>
              </div>
            </div>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

// ── Main App ───────────────────────────────────────────────────────────────
export default function App() {
  const [activeTab, setActiveTab] = useState('home');

  // ── Dynamic sticky-header offset (header height varies by breakpoint —
  //    e.g. it wraps to 2 rows on mobile — so it's measured, not hardcoded) ──
  const headerRef = useRef(null);
  const topbarRef = useRef(null);
  const [headerHeight, setHeaderHeight] = useState(67);
  const [topbarHeight, setTopbarHeight] = useState(66);

  useEffect(() => {
    const measure = () => {
      if (headerRef.current) setHeaderHeight(headerRef.current.getBoundingClientRect().height);
      if (topbarRef.current) setTopbarHeight(topbarRef.current.getBoundingClientRect().height);
    };
    measure();
    window.addEventListener('resize', measure);
    const ro = new ResizeObserver(measure);
    if (headerRef.current) ro.observe(headerRef.current);
    if (topbarRef.current) ro.observe(topbarRef.current);
    return () => { window.removeEventListener('resize', measure); ro.disconnect(); };
  }, [activeTab]);

  // ── Invoice Data State (Travelport live lookup) ───────────────────────────
  const [invoicePnr, setInvoicePnr] = useState('');
  const [invoiceData, setInvoiceData] = useState(null);
  const [invoiceLoading, setInvoiceLoading] = useState(false);
  const [invoiceError, setInvoiceError] = useState('');

  // Report generation state (date-wise)
  const [invoiceMode, setInvoiceMode] = useState('single'); // 'single' | 'report'
  const [reportStartDate, setReportStartDate] = useState(() => { const d = new Date(); d.setDate(d.getDate() - 30); return d.toISOString().split('T')[0]; });
  const [reportEndDate, setReportEndDate] = useState(() => new Date().toISOString().split('T')[0]);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportData, setReportData] = useState([]);
  const [reportError, setReportError] = useState('');

  // Search state
  const [searchType, setSearchType] = useState('oneway'); // 'oneway' | 'roundtrip' | 'multicity'
  const [searchOrigin, setSearchOrigin] = useState('CMB');
  const [searchDest, setSearchDest] = useState('DXB');
  const [searchDate, setSearchDate] = useState(getTomorrow());
  const [returnDate, setReturnDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 8); // return in 8 days default
    return d.toISOString().split('T')[0];
  });
  const [multiCityLegs, setMultiCityLegs] = useState([
    { origin: 'CMB', dest: 'DXB', date: getTomorrow() },
    { origin: 'DXB', dest: 'SIN', date: (() => { const d = new Date(); d.setDate(d.getDate() + 5); return d.toISOString().split('T')[0]; })() }
  ]);
  const [adultCount, setAdultCount] = useState(1);
  const [childCount, setChildCount] = useState(0);
  const [infantCount, setInfantCount] = useState(0);
  const [cabinPref, setCabinPref] = useState('Economy'); // default to Economy as shown in image
  const [showHomePopover, setShowHomePopover] = useState(false);
  const [showResultsPopover, setShowResultsPopover] = useState(false);

  // Flight results
  const [flights, setFlights] = useState([]);
  const [loadingFlights, setLoadingFlights] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [expandedFareIds, setExpandedFareIds] = useState({});
  const [selectedCabins, setSelectedCabins] = useState({});

  // Results page filter state
  const [filterMaxPrice, setFilterMaxPrice] = useState(null);
  const [filterStops, setFilterStops] = useState('any'); // 'any' | '0' | '1+'
  const [filterCabin, setFilterCabin] = useState('any');
  const [filterSource, setFilterSource] = useState('any'); // 'any' | 'GDS' | 'NDC' | 'LCC'
  const [filterTimeOfDay, setFilterTimeOfDay] = useState('any'); // 'any' | 'morning' | 'afternoon' | 'evening'
  const [filterAirlines, setFilterAirlines] = useState([]); // array of selected airline names
  const [sortBy, setSortBy] = useState('price'); // 'price' | 'duration' | 'departure'

  // Selected flight (Step 3)
  const [selectedFlight, setSelectedFlight] = useState(null);

  // Booking wizard step: 'passenger' | 'review' | 'processing' | 'ticket'
  const [bookingStep, setBookingStep] = useState(null);

  // Traveler forms (multiple passengers)
  const [travelers, setTravelers] = useState([]);
  const [activePassengerIdx, setActivePassengerIdx] = useState(0);
  const [bookingError, setBookingError] = useState('');

  // Issued ticket (Step 10 popup)
  const [issuedTicket, setIssuedTicket] = useState(null);
  const [refreshingTicket, setRefreshingTicket] = useState(false);

  // Workbench and seat map states
  const [workbenchId, setWorkbenchId] = useState(null);
  const [offerId, setOfferId] = useState(null);
  const [seatMap, setSeatMap] = useState(null);
  const [selectedSeats, setSelectedSeats] = useState([]);

  // Payment states
  const [paymentMethod, setPaymentMethod] = useState('card');
  const [cardholderName, setCardholderName] = useState('');
  const [cardNumber, setCardNumber] = useState('');
  const [cardExpiry, setCardExpiry] = useState('');
  const [cardCvv, setCardCvv] = useState('');

  // My Bookings
  const [myBookings, setMyBookings] = useState([]);
  const [searchEmail, setSearchEmail] = useState('');
  const [loadingBookings, setLoadingBookings] = useState(false);

  // Notification
  const [notification, setNotification] = useState(null);

  useEffect(() => {
    if (activeTab === 'bookings') fetchBookings();
  }, [activeTab]);

  const showNotification = (message, type = 'success') => {
    let displayMessage = '';
    if (message) {
      if (message instanceof Error) {
        displayMessage = message.message;
      } else if (typeof message === 'object') {
        displayMessage = message.message || message.detail || JSON.stringify(message);
      } else {
        displayMessage = String(message);
      }
    } else {
      displayMessage = 'An unknown error occurred';
    }
    setNotification({ message: displayMessage, type });
    setTimeout(() => setNotification(null), 5000);
  };

  const handleRefreshTicket = async (locatorCode) => {
    if (!locatorCode) return;
    setRefreshingTicket(true);
    showNotification('Refreshing reservation details from GDS...', 'success');
    try {
      const res = await fetchWithRetry(`${API_BASE}/bookings/retrieve/${locatorCode}`);
      const data = await handleApiResponse(res, 'Failed to refresh ticket');
      setIssuedTicket(data);
      showNotification('🔄 Ticket details synchronized with Travelport GDS.', 'success');
      fetchBookings();
    } catch (err) {
      const errMsg = err.message || String(err);
      showNotification(errMsg, 'error');
    } finally {
      setRefreshingTicket(false);
    }
  };

  const handleViewTicket = async (ticket) => {
    setIssuedTicket(ticket);
    setBookingStep('ticket');
    
    // Background PNR refresh
    try {
      const res = await fetchWithRetry(`${API_BASE}/bookings/retrieve/${ticket.locator_code || ticket.pnr}`);
      if (res.ok) {
        const updated = await res.json();
        setIssuedTicket(updated);
        fetchBookings();
      }
    } catch (e) {
      console.warn("Background PNR refresh failed:", e);
    }
  };

  // ── STEP 2: Search Flights ───────────────────────────────────────────────
  const handleSearch = async (e, overrides = {}) => {
    if (e) e.preventDefault();
    setLoadingFlights(true);
    setSearchError('');
    setFlights([]);

    const origin = overrides.origin || searchOrigin;
    const destination = overrides.destination || searchDest;
    const date = overrides.date || searchDate;

    let legs = null;
    let effectiveSearchType = searchType;
    if (overrides.origin || overrides.destination) {
      effectiveSearchType = 'oneway';
      setSearchType('oneway');
    }

    if (effectiveSearchType === 'roundtrip') {
      legs = [
        { origin, destination, departure_date: date },
        { origin: destination, destination: origin, departure_date: returnDate || date }
      ];
    } else if (effectiveSearchType === 'multicity') {
      legs = multiCityLegs.map(l => ({
        origin: l.origin,
        destination: l.dest,
        departure_date: l.date
      }));
    }

    // Guarantee search animation is visible for at least 2.5s for professional feel
    const minLoaderPromise = new Promise(resolve => setTimeout(resolve, 2500));

    try {
      const searchPromise = fetchWithRetry(`${API_BASE}/flights/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          origin: legs ? null : origin,
          destination: legs ? null : destination,
          departure_date: legs ? null : date,
          legs,
          adult_count: adultCount,
          child_count: childCount,
          infant_count: infantCount,
          cabin_preference: cabinPref === 'All' ? null : (cabinPref || null)
        })
      });

      // Wait for both search API and minimum timer to finish
      const [res] = await Promise.all([searchPromise, minLoaderPromise]);

      const data = await handleApiResponse(res, 'Search failed');
      const fetched = data.flights || [];
      setFlights(fetched);
      // Reset filters for new search
      setFilterMaxPrice(null);
      setFilterStops('any');
      setFilterCabin('any');
      setFilterTimeOfDay('any');
      setFilterAirlines([]);
      setSortBy('price');
      if (fetched.length === 0) {
        setSearchError('No flights found for this route and date. Try different criteria.');
      }
    } catch (err) {
      const errMsg = err.message || String(err);
      setSearchError(errMsg);
      showNotification(errMsg, 'error');
    } finally {
      setLoadingFlights(false);
    }
    setActiveTab('results');
  };

  // ── STEP 3: Select Flight ────────────────────────────────────────────────
  const handleSelectFlight = (flight, fareOption) => {
    setSelectedFlight({
      ...flight,
      price: fareOption.price,
      currency: fareOption.currency,
      price_breakdown: fareOption.price_breakdown,
      cabin_class: fareOption.cabin_class,
      fare_family: fareOption.brand_name,
      raw_offering: fareOption.raw_offering,
      baggage_allowance: fareOption.baggage_allowance,
      change_policy: fareOption.change_policy,
      cancel_policy: fareOption.cancel_policy
    });
    setBookingStep('passenger');
    setBookingError('');
    
    const travelerList = [];
    for (let i = 0; i < adultCount; i++) {
      travelerList.push({
        first_name: '', last_name: '', date_of_birth: '',
        gender: 'Male', passport_number: '', passport_expiry: '',
        nationality: 'LK', email: '', phone: '', passenger_type: 'ADT'
      });
    }
    for (let i = 0; i < childCount; i++) {
      travelerList.push({
        first_name: '', last_name: '', date_of_birth: '',
        gender: 'Male', passport_number: '', passport_expiry: '',
        nationality: 'LK', email: '', phone: '', passenger_type: 'CNN'
      });
    }
    for (let i = 0; i < infantCount; i++) {
      travelerList.push({
        first_name: '', last_name: '', date_of_birth: '',
        gender: 'Male', passport_number: '', passport_expiry: '',
        nationality: 'LK', email: '', phone: '', passenger_type: 'INF'
      });
    }
    setTravelers(travelerList);
    setActivePassengerIdx(0);
  };

  const handleTravelerChange = (field, value) => {
    setTravelers(prev => {
      const updated = [...prev];
      updated[activePassengerIdx] = { ...updated[activePassengerIdx], [field]: value };
      return updated;
    });
  };

  const handlePassengerNext = async (e) => {
    e.preventDefault();
    const required = ['first_name', 'last_name', 'date_of_birth', 'passport_number', 'passport_expiry', 'email', 'phone'];
    for (let i = 0; i < travelers.length; i++) {
      const t = travelers[i];
      for (const f of required) {
        if (!t[f]) {
          setBookingError(`Please fill in Passenger ${i + 1} details: ${f.replace(/_/g, ' ')}`);
          setActivePassengerIdx(i);
          return;
        }
        if (f === 'first_name' || f === 'last_name') {
          if (t[f].trim().length < 2) {
            setBookingError(`Passenger ${i + 1} ${f.replace(/_/g, ' ')} must be at least 2 characters.`);
            setActivePassengerIdx(i);
            return;
          }
        }
      }
    }
    setBookingError('');
    setBookingStep('seats_loading');

    try {
      if (travelers.length > 0) {
        setCardholderName(`${travelers[0].first_name} ${travelers[0].last_name}`.toUpperCase());
      }

      const res = await fetchWithRetry(`${API_BASE}/bookings/initiate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          raw_offering: selectedFlight.raw_offering,
          travelers
        })
      });

      const data = await handleApiResponse(res, 'Failed to initialize booking session');

      setWorkbenchId(data.workbench_id);
      setOfferId(data.offer_id);
      setSelectedSeats([]);
      setActivePassengerIdx(0);

      if (data.seat_map_available === false || !data.seat_map) {
        setSeatMap(null);
        // Travelport confirmed no seat map for this airline/flight — skip seat step, go to review
        showNotification('ℹ️ Seat selection is not supported by this airline via GDS. Proceeding directly to payment.', 'info');
        setBookingStep('review');
      } else {
        setSeatMap(data.seat_map);
        setBookingStep('seats');
      }

    } catch (err) {
      const errMsg = err.message || String(err);
      setBookingError(errMsg);
      setBookingStep('passenger');
      showNotification(errMsg, 'error');
    }
  };

  // ── STEPS 4-9: Confirm Booking + Issue Ticket ─────────────────────────────
  const handleConfirmBooking = async () => {
    setBookingStep('processing');
    setBookingError('');

    try {
      // Always pass raw_offering so the backend can create a FRESH workbench at
      // commit time. This fixes the "WORKBENCH ID IS NOT VALID" (error 8506)
      // caused by the Travelport sandbox workbench TTL expiring while the user
      // spends time on the passenger / seat / review / payment steps.
      const res = await fetchWithRetry(`${API_BASE}/bookings/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          raw_offering: selectedFlight.raw_offering,
          travelers,
          selected_seats: selectedSeats,
          payment_method: paymentMethod,
          cabin_class: selectedFlight.cabin_class,
          fare_family: selectedFlight.fare_family,
          custom_price: selectedFlight.price
        })
      });

      const data = await handleApiResponse(res, 'Booking failed');

      setIssuedTicket(data.ticket);
      setBookingStep('ticket');
      showNotification('🎉 Ticket issued successfully!', 'success');

    } catch (err) {
      const errMsg = err.message || String(err);
      setBookingError(errMsg);
      setBookingStep('payment');
      showNotification(errMsg, 'error');
    }
  };

  const closeBookingFlow = () => {
    setSelectedFlight(null);
    setBookingStep(null);
    setIssuedTicket(null);
    setBookingError('');
    setWorkbenchId(null);
    setOfferId(null);
    setSeatMap(null);
    setSelectedSeats([]);
    setCardholderName('');
    setCardNumber('');
    setCardExpiry('');
    setCardCvv('');
    setPaymentMethod('card');
  };

  const renderSeat = (seat, idx) => {
    if (!seat || seat.type === 'empty' || seat.status === 'empty') {
      return <div key={idx} style={{ width: '32px', height: '32px' }} />;
    }

    const isOccupied = seat.status === 'occupied';
    const isAvailable = seat.status === 'available';
    
    // Check if selected by ANY passenger
    const selectedByP = selectedSeats.find(s => s.seat_number === seat.seat_number);
    const isSelectedByActive = selectedByP && selectedByP.passenger_idx === activePassengerIdx;
    const isSelectedByOther = selectedByP && selectedByP.passenger_idx !== activePassengerIdx;

    let seatBg = 'white';
    let seatBorder = '1px solid #cbd5e1';
    let seatColor = 'var(--text-secondary)';
    let cursor = 'pointer';

    if (isOccupied) {
      seatBg = '#cbd5e1';
      seatBorder = '1px solid #94a3b8';
      seatColor = '#94a3b8';
      cursor = 'not-allowed';
    } else if (isSelectedByActive) {
      seatBg = 'var(--gs-crimson)';
      seatBorder = '1px solid var(--gs-crimson)';
      seatColor = 'white';
    } else if (isSelectedByOther) {
      seatBg = '#fee2e2';
      seatBorder = '1px dashed var(--gs-crimson)';
      seatColor = 'var(--gs-crimson)';
      cursor = 'not-allowed';
    } else {
      // Color code by seat type / price
      if (seat.type && (seat.type.includes('Front') || seat.type.includes('FRONT') || seat.price > 5000)) {
        seatBg = '#e0e7ff';
        seatBorder = '1px solid #4f46e5';
        seatColor = '#4f46e5';
      }
    }

    const handleSeatClick = () => {
      if (isOccupied) return;
      if (isSelectedByOther) {
        showNotification(`This seat is already selected by Passenger ${selectedByP.passenger_idx + 1}.`, 'error');
        return;
      }

      // If active passenger already has this seat selected, click deselects it
      if (isSelectedByActive) {
        setSelectedSeats(prev => prev.filter(s => s.passenger_idx !== activePassengerIdx));
      } else {
        // Select seat for active passenger (replaces current seat if they have one)
        const newSelection = {
          passenger_idx: activePassengerIdx,
          seat_number: seat.seat_number,
          price: seat.price || 0.0,
          currency: seat.currency || 'LKR',
          type: seat.type || 'Standard'
        };

        setSelectedSeats(prev => {
          const filtered = prev.filter(s => s.passenger_idx !== activePassengerIdx);
          return [...filtered, newSelection];
        });

        // Automatically advance to the next passenger if there is one without a seat
        setTimeout(() => {
          const nextIndex = travelers.findIndex((t, i) => i > activePassengerIdx && !selectedSeats.some(s => s.passenger_idx === i));
          if (nextIndex !== -1) {
            setActivePassengerIdx(nextIndex);
          } else {
            const firstIndex = travelers.findIndex((t, i) => !selectedSeats.some(s => s.passenger_idx === i) && i !== activePassengerIdx);
            if (firstIndex !== -1) {
              setActivePassengerIdx(firstIndex);
            }
          }
        }, 300);
      }
    };

    return (
      <button
        key={seat.seat_number || idx}
        type="button"
        disabled={isOccupied}
        onClick={handleSeatClick}
        title={`${seat.seat_number} - ${seat.type || 'Seat'} (${seat.price === 0 ? 'Included' : `${seat.currency} ${seat.price?.toLocaleString()}`} with ${selectedFlight.fare_family})`}
        style={{
          width: '32px',
          height: '32px',
          borderRadius: '4px',
          background: seatBg,
          border: seatBorder,
          color: seatColor,
          fontSize: '0.65rem',
          fontWeight: '700',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: cursor,
          outline: 'none',
          boxShadow: isSelectedByActive ? '0 0 8px rgba(195, 18, 46, 0.4)' : 'none',
          transition: 'all 0.15s'
        }}
      >
        {isOccupied ? '×' : seat.seat_number?.slice(-1)}
      </button>
    );
  };

  // ── My Bookings ──────────────────────────────────────────────────────────
  const fetchBookings = async (filterEmail = '') => {
    setLoadingBookings(true);
    try {
      let url = `${API_BASE}/bookings/history`;
      const email = filterEmail || searchEmail;
      if (email) url += `?email=${encodeURIComponent(email)}`;
      const res = await fetchWithRetry(url);
      const data = await handleApiResponse(res, 'Failed to load bookings');
      setMyBookings(data.bookings || []);
    } catch (err) {
      const errMsg = err.message || String(err);
      showNotification(errMsg, 'error');
    } finally {
      setLoadingBookings(false);
    }
  };

  const handleCancelBooking = async (locatorCode) => {
    if (!confirm('Are you sure you want to cancel this booking?')) return;
    try {
      const res = await fetchWithRetry(`${API_BASE}/bookings/${locatorCode}/cancel`, { method: 'POST' });
      const data = await handleApiResponse(res, 'Cancellation failed');
      showNotification('Booking cancelled successfully.', 'warning');
      fetchBookings();
    } catch (err) {
      const errMsg = err.message || String(err);
      showNotification(errMsg, 'error');
    }
  };

  // ── PDF DOWNLOAD & SHARING HANDLERS ───────────────────────────────────────
  const handleDownloadPDF = () => {
    const element = document.getElementById('electronic-ticket-receipt');
    if (!element) return;
    
    showNotification('Generating PDF. Please wait...', 'success');
    
    const opt = {
      margin:       [0.3, 0.3, 0.3, 0.3], // top, left, bottom, right in inches
      filename:     `George_Steuart_Ticket_${issuedTicket.locator_code || 'booking'}.pdf`,
      image:        { type: 'jpeg', quality: 0.98 },
      html2canvas:  { 
        scale: 2, 
        useCORS: true, 
        letterRendering: true,
        scrollX: 0,
        scrollY: 0
      },
      jsPDF:        { unit: 'in', format: 'a4', orientation: 'portrait' }
    };

    if (window.html2pdf) {
      window.html2pdf().set(opt).from(element).save();
    } else {
      const script = document.createElement('script');
      script.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js';
      script.integrity = 'sha512-GsLlZN/3F2ErC5ifS5QtgpiJtWd43JWSuIgh7mbzZ8zBps+dvLusV+eNQATqgA/HdeKFVgA5v3S/cIrLF7QnIg==';
      script.crossOrigin = 'anonymous';
      script.referrerPolicy = 'no-referrer';
      script.onload = () => {
        window.html2pdf().set(opt).from(element).save();
      };
      document.body.appendChild(script);
    }
  };

  const getShareText = () => {
    if (!issuedTicket) return '';
    return `✈️ *George Steuart Travel - Electronic Ticket Receipt*

*Passenger:* ${cleanPassengerName(issuedTicket.passenger_name)}
*PNR / Booking Reference:* ${issuedTicket.locator_code || issuedTicket.pnr}
*Airline PNR:* ${issuedTicket.airline_pnr || 'N/A'}
*Flight:* ${issuedTicket.flight_number} (${issuedTicket.airline})
*Route:* ${issuedTicket.departure_airport} ➔ ${issuedTicket.arrival_airport}
*Departure:* ${issuedTicket.departure_time}
*Arrival:* ${issuedTicket.arrival_time}
*Cabin:* ${issuedTicket.cabin_class}
*Seat:* ${issuedTicket.seat_number || 'Not assigned'}
*Ticket No:* ${issuedTicket.ticket_number || 'PENDING (GDS PNR Confirmed)'}

Thank you for choosing George Steuart Travel (Established 1835). Have a safe flight!`;
  };

  const handleShareWhatsApp = () => {
    const text = encodeURIComponent(getShareText());
    window.open(`https://api.whatsapp.com/send?text=${text}`, '_blank');
  };

  const handleShareEmail = () => {
    const subject = encodeURIComponent(`E-Ticket Confirmation - PNR: ${issuedTicket.locator_code || issuedTicket.pnr}`);
    const body = encodeURIComponent(getShareText().replace(/\*/g, '')); // remove asterisks for clean text
    window.location.href = `mailto:?subject=${subject}&body=${body}`;
  };

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div className="app-container">

      {/* Toast Notification */}
      {notification && (
        <div className={`toast-notification ${notification.type} animate-fade`}>
          <span>{notification.message}</span>
        </div>
      )}

      {/* Header */}
      <header ref={headerRef} className="header glass-panel">
        <div className="logo-container" onClick={() => setActiveTab('home')} style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <img src={gsLogo} alt="George Steuart logo" style={{ height: '40px', objectFit: 'contain' }} />
          <span className="logo-text" style={{ fontSize: '1rem', borderLeft: '1px solid var(--border-color)', paddingLeft: '0.75rem' }}>TRAVEL</span>
        </div>
        <nav className="nav-tabs">
          <button className={`nav-tab ${activeTab === 'home' ? 'active' : ''}`} onClick={() => setActiveTab('home')}>Home</button>
          <button className={`nav-tab ${activeTab === 'book' ? 'active' : ''}`} onClick={() => setActiveTab('book')}>Book Flights</button>
          {flights.length > 0 && (
            <button className={`nav-tab ${activeTab === 'results' ? 'active' : ''}`} onClick={() => setActiveTab('results')}>
              ✈ Results ({flights.length})
            </button>
          )}
          <button className={`nav-tab ${activeTab === 'bookings' ? 'active' : ''}`} onClick={() => setActiveTab('bookings')}>My Bookings</button>
          <button className={`nav-tab ${activeTab === 'invoice' ? 'active' : ''}`} onClick={() => setActiveTab('invoice')} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><rect x="5" y="2" width="14" height="20" rx="2"/><path d="M9 7h6M9 11h6M9 15h4"/></svg>
            Invoice Data
          </button>
        </nav>
      </header>

      {/* ── HOME TAB ────────────────────────────────────────────────────── */}
      {activeTab === 'home' && (
        <div className="home-layout animate-fade">
          <section className="hero-section">
            <div className="hero-content">
              <span className="hero-tagline">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>
                Established 1835 | Legacy of Trust
              </span>
              <h1 className="hero-title">The Future of<span className="title-gradient-red">Global Journeys</span></h1>
              <p className="hero-subtitle">
                Sri Lanka's premier travel partner. Powered by <strong>Travelport TripServices</strong> — real-time global flight inventory, live PNR generation, and instant ticket issuance.
              </p>
              <div className="hero-actions">
                <button className="btn btn-primary" onClick={() => setActiveTab('book')}>
                  Launch Booking Engine
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
                </button>
                <button className="btn btn-secondary" onClick={() => setActiveTab('bookings')}>My Boarding Passes</button>
              </div>
              <div className="hero-badge-container">
                <span className="hero-badge">✅ Travelport TripServices v11</span>
                <span className="hero-badge">🔒 Live GDS Data</span>
              </div>
            </div>
          </section>

          {/* Quick Action Cards */}
          <section style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1.5rem', margin: '0.5rem 0 2rem' }}>

            {/* Book Flights */}
            <div onClick={() => setActiveTab('book')} style={{ cursor: 'pointer', background: 'white', border: '2px solid #f1f5f9', borderRadius: '16px', padding: '2rem 1.75rem', boxShadow: '0 4px 20px rgba(0,0,0,0.06)', transition: 'all 0.2s', display: 'flex', flexDirection: 'column', gap: '0.85rem' }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = '#c3122e'; e.currentTarget.style.boxShadow = '0 8px 32px rgba(195,18,46,0.12)'; e.currentTarget.style.transform = 'translateY(-3px)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = '#f1f5f9'; e.currentTarget.style.boxShadow = '0 4px 20px rgba(0,0,0,0.06)'; e.currentTarget.style.transform = 'translateY(0)'; }}>
              <div style={{ width: '52px', height: '52px', background: 'linear-gradient(135deg, #c3122e, #9b0e24)', borderRadius: '14px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
              </div>
              <div>
                <div style={{ fontWeight: '800', fontSize: '1.1rem', color: '#1e293b', marginBottom: '0.3rem' }}>Book Flights</div>
                <div style={{ fontSize: '0.82rem', color: '#64748b', lineHeight: '1.5' }}>Search live Travelport GDS inventory and book flights in real time with instant PNR generation.</div>
              </div>
              <div style={{ marginTop: 'auto', display: 'flex', alignItems: 'center', gap: '0.4rem', color: '#c3122e', fontWeight: '700', fontSize: '0.82rem' }}>
                Open Booking Engine
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
              </div>
            </div>

            {/* My Bookings */}
            <div onClick={() => setActiveTab('bookings')} style={{ cursor: 'pointer', background: 'white', border: '2px solid #f1f5f9', borderRadius: '16px', padding: '2rem 1.75rem', boxShadow: '0 4px 20px rgba(0,0,0,0.06)', transition: 'all 0.2s', display: 'flex', flexDirection: 'column', gap: '0.85rem' }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = '#0f172a'; e.currentTarget.style.boxShadow = '0 8px 32px rgba(15,23,42,0.12)'; e.currentTarget.style.transform = 'translateY(-3px)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = '#f1f5f9'; e.currentTarget.style.boxShadow = '0 4px 20px rgba(0,0,0,0.06)'; e.currentTarget.style.transform = 'translateY(0)'; }}>
              <div style={{ width: '52px', height: '52px', background: 'linear-gradient(135deg, #0f172a, #1e293b)', borderRadius: '14px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
              </div>
              <div>
                <div style={{ fontWeight: '800', fontSize: '1.1rem', color: '#1e293b', marginBottom: '0.3rem' }}>My Bookings</div>
                <div style={{ fontSize: '0.82rem', color: '#64748b', lineHeight: '1.5' }}>View all bookings, retrieve boarding passes, manage PNRs and cancel reservations.</div>
              </div>
              <div style={{ marginTop: 'auto', display: 'flex', alignItems: 'center', gap: '0.4rem', color: '#1e293b', fontWeight: '700', fontSize: '0.82rem' }}>
                View Bookings
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
              </div>
            </div>
            {/* Invoice Data */}
            <div onClick={() => setActiveTab('invoice')} style={{ cursor: 'pointer', background: 'white', border: '2px solid #f1f5f9', borderRadius: '16px', padding: '2rem 1.75rem', boxShadow: '0 4px 20px rgba(0,0,0,0.06)', transition: 'all 0.2s', display: 'flex', flexDirection: 'column', gap: '0.85rem' }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = '#7c3aed'; e.currentTarget.style.boxShadow = '0 8px 32px rgba(124,58,237,0.12)'; e.currentTarget.style.transform = 'translateY(-3px)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = '#f1f5f9'; e.currentTarget.style.boxShadow = '0 4px 20px rgba(0,0,0,0.06)'; e.currentTarget.style.transform = 'translateY(0)'; }}>
              <div style={{ width: '52px', height: '52px', background: 'linear-gradient(135deg, #7c3aed, #5b21b6)', borderRadius: '14px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
              </div>
              <div>
                <div style={{ fontWeight: '800', fontSize: '1.1rem', color: '#1e293b', marginBottom: '0.3rem' }}>Invoice Data</div>
                <div style={{ fontSize: '0.82rem', color: '#64748b', lineHeight: '1.5' }}>Retrieve live PNR data from Travelport — full passenger details, fare breakdown, tax breakdown and export.</div>
              </div>
              <div style={{ marginTop: 'auto', display: 'flex', alignItems: 'center', gap: '0.4rem', color: '#7c3aed', fontWeight: '700', fontSize: '0.82rem' }}>
                Retrieve Invoice
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
              </div>
            </div>

          </section>

          {/* Features */}
          <section className="features-section">
            <div className="section-header">
              <span className="section-label">Powered by Travelport</span>
              <h2 className="section-heading">Enterprise-Grade Flight Booking</h2>
            </div>
            <div className="features-grid">
              <div className="feature-card">
                <div className="feature-icon-box"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10"/></svg></div>
                <h4>Live GDS Inventory</h4>
                <p>Real-time flight availability from Travelport's global distribution system — no cached or mock data.</p>
              </div>
              <div className="feature-card">
                <div className="feature-icon-box"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg></div>
                <h4>Instant PNR Generation</h4>
                <p>Live workbench commits to the Travelport GDS generate a real PNR in seconds.</p>
              </div>
              <div className="feature-card">
                <div className="feature-icon-box"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></div>
                <h4>Electronic Ticketing</h4>
                <p>Full e-ticket issuance through Travelport TripServices v11 with real ticket numbers.</p>
              </div>
            </div>
          </section>

          {/* Footer */}
          <footer className="footer">
            <div className="footer-brand" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <img src="https://georgesteuart.lk/img/footer-logo.jpg" alt="George Steuart logo" style={{ height: '35px', alignSelf: 'flex-start', objectFit: 'contain' }} />
              <span className="footer-copy">© {new Date().getFullYear()} George Steuart Travel Ltd. All rights reserved.</span>
            </div>
            <div className="footer-links">
              <a href="#" className="footer-link">Privacy Policy</a>
              <a href="#" className="footer-link">Terms of Service</a>
              <a href="#" className="footer-link">Contact Support</a>
            </div>
          </footer>
        </div>
      )}

      {/* ── BOOK FLIGHTS TAB (search form only) ─────────────────────────── */}
      {activeTab === 'book' && (
        <div className="tab-content animate-fade">
          <section className="search-section glass-panel">
            <h2 className="section-title">Find Your Flight</h2>
            
            <div className="search-type-tabs">
              <button type="button" className={`search-type-tab ${searchType === 'oneway' ? 'active' : ''}`} onClick={() => setSearchType('oneway')}>One Way</button>
              <button type="button" className={`search-type-tab ${searchType === 'roundtrip' ? 'active' : ''}`} onClick={() => setSearchType('roundtrip')}>Round Trip</button>
              <button type="button" className={`search-type-tab ${searchType === 'multicity' ? 'active' : ''}`} onClick={() => setSearchType('multicity')}>Multi City</button>
            </div>

            <form onSubmit={handleSearch} className="search-form">
              {searchType === 'multicity' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '1.25rem' }}>
                  {multiCityLegs.map((leg, index) => (
                    <div key={index} className="form-row multi-city-row" style={{ flexWrap: 'wrap', gap: '1rem', alignItems: 'flex-end' }}>
                      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', minWidth: '80px' }}>
                        <span className="leg-badge">Flight {index + 1}</span>
                      </div>
                      <AirportSearchSelect
                        value={leg.origin}
                        onChange={(val) => {
                          setMultiCityLegs(prev => {
                            const updated = [...prev];
                            updated[index] = { ...updated[index], origin: val };
                            return updated;
                          });
                        }}
                        label="From (Origin)"
                        placeholder="Search origin city/airport..."
                      />
                      <div className="search-divider" style={{ alignSelf: 'center', marginTop: '1.2rem' }}>
                        →
                      </div>
                      <AirportSearchSelect
                        value={leg.dest}
                        onChange={(val) => {
                          setMultiCityLegs(prev => {
                            const updated = [...prev];
                            updated[index] = { ...updated[index], dest: val };
                            return updated;
                          });
                        }}
                        label="To (Destination)"
                        placeholder="Search destination city/airport..."
                      />
                      <div className="form-group" style={{ minWidth: '160px' }}>
                        <label className="form-label">Departure Date</label>
                        <input 
                          type="date" 
                          className="form-input" 
                          min={index > 0 ? multiCityLegs[index-1].date : getTomorrow()} 
                          value={leg.date} 
                          onChange={e => {
                            setMultiCityLegs(prev => {
                              const updated = [...prev];
                              updated[index] = { ...updated[index], date: e.target.value };
                              for (let i = index + 1; i < updated.length; i++) {
                                if (updated[i].date < e.target.value) {
                                  updated[i].date = e.target.value;
                                }
                              }
                              return updated;
                            });
                          }} 
                          required
                        />
                      </div>
                      {multiCityLegs.length > 2 && (
                        <button 
                          type="button" 
                          className="btn btn-danger btn-sm" 
                          onClick={() => {
                            setMultiCityLegs(prev => prev.filter((_, i) => i !== index));
                          }}
                          style={{ marginBottom: '1.25rem', padding: '0.75rem' }}
                          title="Remove this leg"
                        >
                          ✕
                        </button>
                      )}
                    </div>
                  ))}
                  {multiCityLegs.length < 5 && (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => {
                        setMultiCityLegs(prev => {
                          const lastLeg = prev[prev.length - 1];
                          return [
                            ...prev,
                            { origin: lastLeg.dest, dest: '', date: lastLeg.date }
                          ];
                        });
                      }}
                      style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: '0.25rem' }}
                    >
                      ➕ Add Flight
                    </button>
                  )}
                </div>
              ) : (
                <>
                  <div className="form-row" style={{ flexWrap: 'wrap', gap: '1rem', marginBottom: '0.75rem' }}>
                    <AirportSearchSelect value={searchOrigin} onChange={setSearchOrigin} label="From (Origin)" placeholder="Search origin city/airport..." />
                    <div className="search-divider" style={{ alignSelf: 'center', marginTop: '1.2rem' }}>
                      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M8 7h12M20 7l-4-4M20 7l-4 4M16 17H4M4 17l4 4M4 17l4-4"/></svg>
                    </div>
                    <AirportSearchSelect value={searchDest} onChange={setSearchDest} label="To (Destination)" placeholder="Search destination city/airport..." />
                  </div>
                  <div className="form-row" style={{ flexWrap: 'wrap', gap: '1rem', marginBottom: '0.75rem' }}>
                    <div className="form-group" style={{ flex: 1, minWidth: '160px' }}>
                      <label className="form-label">Departure Date</label>
                      <input type="date" className="form-input" min={getTomorrow()} value={searchDate} onChange={e => {
                        setSearchDate(e.target.value);
                        if (returnDate && returnDate < e.target.value) {
                          setReturnDate(e.target.value);
                        }
                      }} required />
                    </div>
                    {searchType === 'roundtrip' && (
                      <div className="form-group" style={{ flex: 1, minWidth: '160px' }}>
                        <label className="form-label">Return Date</label>
                        <input type="date" className="form-input" min={searchDate || getTomorrow()} value={returnDate} onChange={e => setReturnDate(e.target.value)} required />
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Shared Passenger & Cabin Class options */}
              <div className="form-row" style={{ marginTop: '0.75rem', position: 'relative' }}>
                <div className="form-group" style={{ flex: 1, minWidth: '220px', position: 'relative' }}>
                  <label className="form-label">Travelers & Cabin Class</label>
                  <button
                    type="button"
                    className="form-input text-left"
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      width: '100%',
                      background: 'white',
                      textAlign: 'left',
                      cursor: 'pointer',
                      padding: '0.625rem 0.75rem',
                      border: '1.5px solid var(--border-color)',
                      borderRadius: '6px',
                      fontWeight: '600',
                      color: 'var(--text-primary)',
                      minHeight: '42px',
                      fontFamily: 'var(--font-body)',
                      outline: 'none'
                    }}
                    onClick={() => setShowHomePopover(!showHomePopover)}
                  >
                    <span>
                      {adultCount + childCount + infantCount} Traveler{adultCount + childCount + infantCount > 1 ? 's' : ''}, {cabinPref === 'PremiumEconomy' ? 'Premium Economy' : cabinPref === 'All' ? 'All Classes' : cabinPref}
                    </span>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ marginLeft: '8px', color: '#64748b', transition: 'transform 0.2s', transform: showHomePopover ? 'rotate(180deg)' : 'none' }}>
                      <path d="M6 9l6 6 6-6" />
                    </svg>
                  </button>

                  {showHomePopover && (
                    <>
                      {/* Click-outside backdrop */}
                      <div
                        style={{
                          position: 'fixed',
                          top: 0,
                          left: 0,
                          right: 0,
                          bottom: 0,
                          zIndex: 999,
                          background: 'transparent'
                        }}
                        onClick={() => setShowHomePopover(false)}
                      />
                      
                      {/* Popover Card */}
                      <div
                        className="traveler-popover-card"
                        style={{
                          position: 'absolute',
                          top: '100%',
                          left: 0,
                          width: '320px',
                          backgroundColor: 'white',
                          borderRadius: '12px',
                          boxShadow: '0 10px 30px rgba(0, 0, 0, 0.15)',
                          border: '1px solid #cbd5e1',
                          padding: '1.25rem',
                          zIndex: 1000,
                          marginTop: '0.5rem',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '1rem',
                          fontFamily: 'var(--font-body)'
                        }}
                      >
                        {/* Header with Close Button */}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', position: 'relative', height: '0px' }}>
                          <button
                            type="button"
                            style={{
                              position: 'absolute',
                              top: '-32px',
                              right: '-8px',
                              background: '#1e3a8a', // Dark blue background
                              color: 'white',
                              border: 'none',
                              borderRadius: '50%',
                              width: '24px',
                              height: '24px',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              cursor: 'pointer',
                              boxShadow: '0 2px 5px rgba(0,0,0,0.2)',
                              zIndex: 1001,
                              outline: 'none',
                              padding: 0
                            }}
                            onClick={() => setShowHomePopover(false)}
                          >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                              <path d="M18 6L6 18M6 6l12 12" />
                            </svg>
                          </button>
                        </div>

                        {/* Row: Adults */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '0.75rem', borderBottom: '1.5px solid #e2e8f0' }}>
                          <div style={{ display: 'flex', flexDirection: 'column' }}>
                            <span style={{ fontWeight: '700', color: '#475569', fontSize: '0.95rem' }}>Adults</span>
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                            <button
                              type="button"
                              disabled={adultCount <= 1}
                              onClick={() => setAdultCount(prev => Math.max(1, prev - 1))}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: adultCount <= 1 ? 'not-allowed' : 'pointer',
                                opacity: adultCount <= 1 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              –
                            </button>
                            <span style={{ minWidth: '16px', textAlign: 'center', fontWeight: '700', color: '#1e293b', fontSize: '1.1rem' }}>{adultCount}</span>
                            <button
                              type="button"
                              disabled={adultCount >= 9}
                              onClick={() => setAdultCount(prev => Math.min(9, prev + 1))}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: adultCount >= 9 ? 'not-allowed' : 'pointer',
                                opacity: adultCount >= 9 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              +
                            </button>
                          </div>
                        </div>

                        {/* Row: Child */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '0.75rem', borderBottom: '1.5px solid #e2e8f0' }}>
                          <div style={{ display: 'flex', flexDirection: 'column' }}>
                            <span style={{ fontWeight: '700', color: '#475569', fontSize: '0.95rem' }}>
                              Child <span style={{ fontWeight: '400', fontSize: '0.8rem', color: '#94a3b8' }}>(2-11 YRS)</span>
                            </span>
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                            <button
                              type="button"
                              disabled={childCount <= 0}
                              onClick={() => setChildCount(prev => Math.max(0, prev - 1))}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: childCount <= 0 ? 'not-allowed' : 'pointer',
                                opacity: childCount <= 0 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              –
                            </button>
                            <span style={{ minWidth: '16px', textAlign: 'center', fontWeight: '700', color: '#1e293b', fontSize: '1.1rem' }}>{childCount}</span>
                            <button
                              type="button"
                              disabled={childCount >= 8}
                              onClick={() => setChildCount(prev => Math.min(8, prev + 1))}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: childCount >= 8 ? 'not-allowed' : 'pointer',
                                opacity: childCount >= 8 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              +
                            </button>
                          </div>
                        </div>

                        {/* Row: Infant */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '0.75rem', borderBottom: '1.5px solid #e2e8f0' }}>
                          <div style={{ display: 'flex', flexDirection: 'column' }}>
                            <span style={{ fontWeight: '700', color: '#475569', fontSize: '0.95rem' }}>
                              Infant <span style={{ fontWeight: '400', fontSize: '0.8rem', color: '#94a3b8' }}>(Below 2 YRS)</span>
                            </span>
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                            <button
                              type="button"
                              disabled={infantCount <= 0}
                              onClick={() => setInfantCount(prev => Math.max(0, prev - 1))}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: infantCount <= 0 ? 'not-allowed' : 'pointer',
                                opacity: infantCount <= 0 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              –
                            </button>
                            <span style={{ minWidth: '16px', textAlign: 'center', fontWeight: '700', color: '#1e293b', fontSize: '1.1rem' }}>{infantCount}</span>
                            <button
                              type="button"
                              disabled={infantCount >= 8}
                              onClick={() => setInfantCount(prev => Math.min(8, prev + 1))}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: infantCount >= 8 ? 'not-allowed' : 'pointer',
                                opacity: infantCount >= 8 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              +
                            </button>
                          </div>
                        </div>

                        {/* Cabin buttons */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                          {[
                            { value: 'All', display: 'All Cabin Classes' },
                            { value: 'Economy', display: 'Economy' },
                            { value: 'PremiumEconomy', display: 'Premium Economy' },
                            { value: 'Business', display: 'Business' },
                            { value: 'First', display: 'First' }
                          ].map((opt) => {
                            const isSelected = cabinPref === opt.value;
                            return (
                              <button
                                key={opt.value}
                                type="button"
                                onClick={() => setCabinPref(opt.value)}
                                style={{
                                  width: '100%',
                                  padding: '0.65rem',
                                  borderRadius: '6px',
                                  border: isSelected ? 'none' : '1.5px solid #cbd5e1',
                                  backgroundColor: isSelected ? '#374151' : 'white',
                                  color: isSelected ? 'white' : '#64748b',
                                  fontWeight: '700',
                                  fontSize: '0.95rem',
                                  cursor: 'pointer',
                                  textAlign: 'center',
                                  transition: 'all 0.15s ease',
                                  outline: 'none'
                                }}
                              >
                                {opt.display}
                              </button>
                            );
                          })}
                        </div>

                        {/* Done button */}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '0.25rem' }}>
                          <button
                            type="button"
                            onClick={() => setShowHomePopover(false)}
                            style={{
                              backgroundColor: '#71717a',
                              color: 'white',
                              border: 'none',
                              borderRadius: '4px',
                              padding: '0.5rem 1.25rem',
                              fontWeight: '600',
                              fontSize: '0.9rem',
                              cursor: 'pointer',
                              transition: 'background-color 0.15s ease',
                              outline: 'none'
                            }}
                          >
                            Done
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              </div>

              <div className="search-buttons">
                <button type="submit" className="btn btn-primary" disabled={loadingFlights}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                  {loadingFlights ? 'Searching Travelport...' : 'Search Live Flights'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      {/* ── FLIGHT RESULTS PAGE ───────────────────────────────────────────── */}
      {activeTab === 'results' && (() => {
        // Compute min/max prices for the filter slider
        const prices = flights.map(f => f.price || 0);
        const minPrice = prices.length ? Math.floor(Math.min(...prices)) : 0;
        const maxPrice = prices.length ? Math.ceil(Math.max(...prices)) : 5000;
        const sliderMax = filterMaxPrice ?? maxPrice;

        // Dynamic unique list of airlines in current search results
        const uniqueAirlines = Array.from(new Set(flights.map(f => f.airline).filter(Boolean)));

        // Apply filters + sort
        let displayed = flights.filter(f => {
          if ((filterMaxPrice !== null) && (f.price || 0) > filterMaxPrice) return false;
          if (filterStops === '0' && (f.stops || 0) !== 0) return false;
          if (filterStops === '1+' && (f.stops || 0) === 0) return false;
          if (filterCabin !== 'any' && f.cabin_class !== filterCabin) return false;
          if (filterSource !== 'any' && (f.fare_source || 'GDS') !== filterSource) return false;

          // Time of day filter
          if (filterTimeOfDay !== 'any') {
            const depTime = f.departure_time?.split('T')[1] || f.departure_time?.split(' ')[1] || '';
            if (depTime) {
              const hour = parseInt(depTime.split(':')[0]);
              if (filterTimeOfDay === 'morning' && hour >= 12) return false;
              if (filterTimeOfDay === 'afternoon' && (hour < 12 || hour >= 18)) return false;
              if (filterTimeOfDay === 'evening' && hour < 18) return false;
            }
          }

          // Airline filter
          if (filterAirlines.length > 0 && !filterAirlines.includes(f.airline)) return false;

          return true;
        });

        if (sortBy === 'price') displayed.sort((a,b) => (a.price||0) - (b.price||0));
        else if (sortBy === 'departure') displayed.sort((a,b) => (a.departure_time||'').localeCompare(b.departure_time||''));
        else if (sortBy === 'duration') {
          const parseDur = (d) => {
            if (!d) return 0;
            const hMatch = d.match(/(\d+)H/);
            const mMatch = d.match(/(\d+)M/);
            const h = hMatch ? parseInt(hMatch[1]) * 60 : 0;
            const m = mMatch ? parseInt(mMatch[1]) : 0;
            return h + m;
          };
          displayed.sort((a,b) => parseDur(a.duration) - parseDur(b.duration));
        }

        const handleAirlineCheckboxChange = (airlineName) => {
          setFilterAirlines(prev => 
            prev.includes(airlineName) 
              ? prev.filter(name => name !== airlineName)
              : [...prev, airlineName]
          );
        };

        return (
          <div className="results-page">
            {/* ── FIXED search topbar (never scrolls) ── */}
            <div ref={topbarRef} className="results-topbar" style={{ top: `${headerHeight}px` }}>
              <form onSubmit={handleSearch} className="results-search-bar">
                <div className="rsb-field">
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="rsb-label">FROM</div>
                    <AirportSearchSelect value={searchOrigin} onChange={setSearchOrigin} label="" placeholder="Origin" />
                  </div>
                </div>
                <div className="rsb-swap" title="Swap airports" onClick={() => { const t = searchOrigin; setSearchOrigin(searchDest); setSearchDest(t); }}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M8 7h12M20 7l-4-4M20 7l-4 4M16 17H4M4 17l4 4M4 17l4-4"/></svg>
                </div>
                <div className="rsb-field">
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="rsb-label">TO</div>
                    <AirportSearchSelect value={searchDest} onChange={setSearchDest} label="" placeholder="Destination" />
                  </div>
                </div>
                <div className="rsb-divider" />
                <div className="rsb-field rsb-field-sm">
                  <div style={{ flex: 1 }}>
                    <div className="rsb-label">DATE</div>
                    <input type="date" className="rsb-input" min={getTomorrow()} value={searchDate} onChange={e => setSearchDate(e.target.value)} required />
                  </div>
                </div>
                {searchType === 'roundtrip' && (
                  <>
                    <div className="rsb-divider" />
                    <div className="rsb-field rsb-field-sm">
                      <div style={{ flex: 1 }}>
                        <div className="rsb-label">RETURN</div>
                        <input type="date" className="rsb-input" min={searchDate || getTomorrow()} value={returnDate} onChange={e => setReturnDate(e.target.value)} required />
                      </div>
                    </div>
                  </>
                )}
                <div className="rsb-field" style={{ flex: '0 1 200px', position: 'relative', cursor: 'pointer' }} onClick={() => setShowResultsPopover(!showResultsPopover)}>
                  <div style={{ flex: 1 }}>
                    <div className="rsb-label">TRAVELERS & CLASS</div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                      <span style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                        {adultCount + childCount + infantCount} Pax, {cabinPref === 'PremiumEconomy' ? 'Prem Eco' : cabinPref}
                      </span>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ marginLeft: '4px', color: '#64748b', transition: 'transform 0.2s', transform: showResultsPopover ? 'rotate(180deg)' : 'none' }}>
                        <path d="M6 9l6 6 6-6" />
                      </svg>
                    </div>
                  </div>

                  {showResultsPopover && (
                    <>
                      {/* Click-outside backdrop */}
                      <div
                        style={{
                          position: 'fixed',
                          top: 0,
                          left: 0,
                          right: 0,
                          bottom: 0,
                          zIndex: 999,
                          background: 'transparent'
                        }}
                        onClick={(e) => {
                          e.stopPropagation();
                          setShowResultsPopover(false);
                        }}
                      />
                      
                      {/* Popover Card */}
                      <div
                        className="traveler-popover-card"
                        style={{
                          position: 'absolute',
                          top: '100%',
                          right: 0,
                          width: '320px',
                          backgroundColor: 'white',
                          borderRadius: '12px',
                          boxShadow: '0 10px 30px rgba(0, 0, 0, 0.15)',
                          border: '1px solid #cbd5e1',
                          padding: '1.25rem',
                          zIndex: 1000,
                          marginTop: '0.5rem',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '1rem',
                          fontFamily: 'var(--font-body)',
                          cursor: 'default'
                        }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        {/* Header with Close Button */}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', position: 'relative', height: '0px' }}>
                          <button
                            type="button"
                            style={{
                              position: 'absolute',
                              top: '-32px',
                              right: '-8px',
                              background: '#1e3a8a', // Dark blue background
                              color: 'white',
                              border: 'none',
                              borderRadius: '50%',
                              width: '24px',
                              height: '24px',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              cursor: 'pointer',
                              boxShadow: '0 2px 5px rgba(0,0,0,0.2)',
                              zIndex: 1001,
                              outline: 'none',
                              padding: 0
                            }}
                            onClick={(e) => {
                              e.stopPropagation();
                              setShowResultsPopover(false);
                            }}
                          >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                              <path d="M18 6L6 18M6 6l12 12" />
                            </svg>
                          </button>
                        </div>

                        {/* Row: Adults */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '0.75rem', borderBottom: '1.5px solid #e2e8f0' }}>
                          <div style={{ display: 'flex', flexDirection: 'column' }}>
                            <span style={{ fontWeight: '700', color: '#475569', fontSize: '0.95rem' }}>Adults</span>
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                            <button
                              type="button"
                              disabled={adultCount <= 1}
                              onClick={(e) => {
                                e.stopPropagation();
                                setAdultCount(prev => Math.max(1, prev - 1));
                              }}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: adultCount <= 1 ? 'not-allowed' : 'pointer',
                                opacity: adultCount <= 1 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              –
                            </button>
                            <span style={{ minWidth: '16px', textAlign: 'center', fontWeight: '700', color: '#1e293b', fontSize: '1.1rem' }}>{adultCount}</span>
                            <button
                              type="button"
                              disabled={adultCount >= 9}
                              onClick={(e) => {
                                e.stopPropagation();
                                setAdultCount(prev => Math.min(9, prev + 1));
                              }}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: adultCount >= 9 ? 'not-allowed' : 'pointer',
                                opacity: adultCount >= 9 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              +
                            </button>
                          </div>
                        </div>

                        {/* Row: Child */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '0.75rem', borderBottom: '1.5px solid #e2e8f0' }}>
                          <div style={{ display: 'flex', flexDirection: 'column' }}>
                            <span style={{ fontWeight: '700', color: '#475569', fontSize: '0.95rem' }}>
                              Child <span style={{ fontWeight: '400', fontSize: '0.8rem', color: '#94a3b8' }}>(2-11 YRS)</span>
                            </span>
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                            <button
                              type="button"
                              disabled={childCount <= 0}
                              onClick={(e) => {
                                e.stopPropagation();
                                setChildCount(prev => Math.max(0, prev - 1));
                              }}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: childCount <= 0 ? 'not-allowed' : 'pointer',
                                opacity: childCount <= 0 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              –
                            </button>
                            <span style={{ minWidth: '16px', textAlign: 'center', fontWeight: '700', color: '#1e293b', fontSize: '1.1rem' }}>{childCount}</span>
                            <button
                              type="button"
                              disabled={childCount >= 8}
                              onClick={(e) => {
                                e.stopPropagation();
                                setChildCount(prev => Math.min(8, prev + 1));
                              }}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: childCount >= 8 ? 'not-allowed' : 'pointer',
                                opacity: childCount >= 8 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              +
                            </button>
                          </div>
                        </div>

                        {/* Row: Infant */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '0.75rem', borderBottom: '1.5px solid #e2e8f0' }}>
                          <div style={{ display: 'flex', flexDirection: 'column' }}>
                            <span style={{ fontWeight: '700', color: '#475569', fontSize: '0.95rem' }}>
                              Infant <span style={{ fontWeight: '400', fontSize: '0.8rem', color: '#94a3b8' }}>(Below 2 YRS)</span>
                            </span>
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                            <button
                              type="button"
                              disabled={infantCount <= 0}
                              onClick={(e) => {
                                e.stopPropagation();
                                setInfantCount(prev => Math.max(0, prev - 1));
                              }}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: infantCount <= 0 ? 'not-allowed' : 'pointer',
                                opacity: infantCount <= 0 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              –
                            </button>
                            <span style={{ minWidth: '16px', textAlign: 'center', fontWeight: '700', color: '#1e293b', fontSize: '1.1rem' }}>{infantCount}</span>
                            <button
                              type="button"
                              disabled={infantCount >= 8}
                              onClick={(e) => {
                                e.stopPropagation();
                                setInfantCount(prev => Math.min(8, prev + 1));
                              }}
                              style={{
                                width: '32px',
                                height: '32px',
                                borderRadius: '50%',
                                border: '1.5px solid #cbd5e1',
                                background: 'white',
                                color: '#475569',
                                fontSize: '1.4rem',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: infantCount >= 8 ? 'not-allowed' : 'pointer',
                                opacity: infantCount >= 8 ? 0.5 : 1,
                                outline: 'none',
                                padding: 0,
                                userSelect: 'none'
                              }}
                            >
                              +
                            </button>
                          </div>
                        </div>

                        {/* Cabin buttons */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                          {[
                            { value: 'Economy', display: 'Economy' },
                            { value: 'PremiumEconomy', display: 'Premium Economy' },
                            { value: 'Business', display: 'Business' },
                            { value: 'First', display: 'First' }
                          ].map((opt) => {
                            const isSelected = cabinPref === opt.value;
                            return (
                              <button
                                key={opt.value}
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setCabinPref(opt.value);
                                }}
                                style={{
                                  width: '100%',
                                  padding: '0.65rem',
                                  borderRadius: '6px',
                                  border: isSelected ? 'none' : '1.5px solid #cbd5e1',
                                  backgroundColor: isSelected ? '#374151' : 'white',
                                  color: isSelected ? 'white' : '#64748b',
                                  fontWeight: '700',
                                  fontSize: '0.95rem',
                                  cursor: 'pointer',
                                  textAlign: 'center',
                                  transition: 'all 0.15s ease',
                                  outline: 'none'
                                }}
                              >
                                {opt.display}
                              </button>
                            );
                          })}
                        </div>

                        {/* Done button */}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '0.25rem' }}>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setShowResultsPopover(false);
                            }}
                            style={{
                              backgroundColor: '#71717a',
                              color: 'white',
                              border: 'none',
                              borderRadius: '4px',
                              padding: '0.5rem 1.25rem',
                              fontWeight: '600',
                              fontSize: '0.9rem',
                              cursor: 'pointer',
                              transition: 'background-color 0.15s ease',
                              outline: 'none'
                            }}
                          >
                            Done
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </div>
                <button type="submit" className="rsb-search-btn" disabled={loadingFlights}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                  Search
                </button>
              </form>
            </div>

            {/* ── Body: sidebar + results (offset for fixed search bar) ── */}
            <div className="results-page-content" style={{ paddingTop: `${headerHeight + topbarHeight}px` }}>

              {/* Filter Sidebar (detailed price range + all options) */}
              <aside className="results-sidebar" style={{ top: `${67 + 66}px`, height: `calc(100vh - ${67 + 66}px)` }}>
                <div className="sidebar-header">Refine Results</div>

                {/* Sort Group */}
                <div className="filter-group">
                  <div className="filter-group-title">Sort By</div>
                  {[['price','Lowest Price'],['departure','Earliest Departure'],['duration','Shortest Duration']].map(([val,lbl]) => (
                    <label key={val} className={`filter-radio-row ${sortBy === val ? 'active' : ''}`}>
                      <input type="radio" name="sort" value={val} checked={sortBy === val} onChange={() => setSortBy(val)} />
                      {lbl}
                    </label>
                  ))}
                </div>

                {/* Stops Group */}
                <div className="filter-group">
                  <div className="filter-group-title">Stops</div>
                  {[['any','Any'],['0','Non-stop only'],['1+','1+ Stop']].map(([val,lbl]) => (
                    <label key={val} className={`filter-radio-row ${filterStops === val ? 'active' : ''}`}>
                      <input type="radio" name="stops" value={val} checked={filterStops === val} onChange={() => setFilterStops(val)} />
                      {lbl}
                    </label>
                  ))}
                </div>

                {/* Cabin Class Group */}
                <div className="filter-group">
                  <div className="filter-group-title">Cabin Class</div>
                  {[['any','All Classes'],['Economy','Economy'],['PremiumEconomy','Premium Economy'],['Business','Business'],['First','First Class']].map(([val,lbl]) => (
                    <label key={val} className={`filter-radio-row ${filterCabin === val ? 'active' : ''}`}>
                      <input type="radio" name="cabin" value={val} checked={filterCabin === val} onChange={() => setFilterCabin(val)} />
                      {lbl}
                    </label>
                  ))}
                </div>

                {/* Fare Type Group */}
                <div className="filter-group">
                  <div className="filter-group-title">Fare Type</div>
                  {[['any','All Fares'],['GDS','GDS (Traditional)'],['NDC','NDC (New Distribution)'],['LCC','LCC (Low-Cost Airline)']].map(([val,lbl]) => (
                    <label key={val} className={`filter-radio-row ${filterSource === val ? 'active' : ''}`}>
                      <input type="radio" name="faresource" value={val} checked={filterSource === val} onChange={() => setFilterSource(val)} />
                      {lbl}
                    </label>
                  ))}
                </div>

                {/* Time of Day Group */}
                <div className="filter-group">
                  <div className="filter-group-title">Departure Time</div>
                  {[['any','Any Time'],['morning','Morning (before 12 PM)'],['afternoon','Afternoon (12 PM - 6 PM)'],['evening','Evening (after 6 PM)']].map(([val,lbl]) => (
                    <label key={val} className={`filter-radio-row ${filterTimeOfDay === val ? 'active' : ''}`}>
                      <input type="radio" name="time" value={val} checked={filterTimeOfDay === val} onChange={() => setFilterTimeOfDay(val)} />
                      {lbl}
                    </label>
                  ))}
                </div>

                {/* Dynamic Airlines Group */}
                {uniqueAirlines.length > 0 && (
                  <div className="filter-group">
                    <div className="filter-group-title">Airlines</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', marginTop: '0.25rem' }}>
                      {uniqueAirlines.map(airline => (
                        <label key={airline} className="filter-radio-row" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.8rem', cursor: 'pointer' }}>
                          <input 
                            type="checkbox" 
                            checked={filterAirlines.includes(airline)} 
                            onChange={() => handleAirlineCheckboxChange(airline)}
                            style={{ accentColor: 'var(--gs-crimson)', width: '13px', height: '13px', cursor: 'pointer' }}
                          />
                          {airline}
                        </label>
                      ))}
                    </div>
                  </div>
                )}

                {/* Max Price Group */}
                {prices.length > 0 && (
                  <div className="filter-group">
                    <div className="filter-group-title">Max Price</div>
                    <div className="filter-price-display">{flights[0]?.currency || 'USD'} {sliderMax.toLocaleString()}</div>
                    <input type="range" min={minPrice} max={maxPrice} step="10"
                      value={sliderMax}
                      onChange={e => setFilterMaxPrice(Number(e.target.value))}
                      className="filter-range" />
                    <div className="filter-price-minmax">
                      <span>{flights[0]?.currency} {minPrice}</span>
                      <span>{flights[0]?.currency} {maxPrice}</span>
                    </div>
                    {filterMaxPrice !== null && <button className="filter-reset-btn" onClick={() => setFilterMaxPrice(null)}>Reset Price</button>}
                  </div>
                )}

                <button className="filter-reset-btn filter-reset-all"
                  onClick={() => { setFilterMaxPrice(null); setFilterStops('any'); setFilterCabin('any'); setFilterSource('any'); setFilterTimeOfDay('any'); setFilterAirlines([]); setSortBy('price'); }}>
                  Reset All Filters
                </button>
              </aside>

              {/* Main Results */}
              <main className="results-main">
                <div className="results-count-bar">
                  <div className="results-count-text">
                    {loadingFlights ? 'Searching Travelport GDS...' : (
                      <><strong>{displayed.length}</strong> of <strong>{flights.length}</strong> flights · <strong>{searchOrigin}</strong> → <strong>{searchDest}</strong> · {searchDate}</>
                    )}
                  </div>
                  <div className="results-live-badge">● Live GDS Data</div>
                </div>


                {!loadingFlights && searchError && (
                  <div className="empty-state glass-panel">
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>
                    <p>{searchError}</p>
                  </div>
                )}

                {!loadingFlights && !searchError && displayed.length === 0 && flights.length > 0 && (
                  <div className="empty-state glass-panel">
                    <p>No flights match your filters.<br />
                      <button className="filter-reset-btn" style={{ marginTop: '0.5rem' }}
                        onClick={() => { setFilterMaxPrice(null); setFilterStops('any'); setFilterCabin('any'); setFilterSource('any'); setFilterTimeOfDay('any'); setFilterAirlines([]); }}>
                        Clear all filters
                      </button>
                    </p>
                  </div>
                )}

                {!loadingFlights && displayed.map((flight, idx) => {
                  const isFareExpanded = !!expandedFareIds[flight.offer_id || idx];
                  const totalPax = adultCount + childCount + infantCount;

                  const fId = flight.offer_id || idx;
                  const activeCabin = selectedCabins[fId] || (flight.cabin_class || (flight.fare_options && flight.fare_options.length > 0 ? flight.fare_options[0].cabin_class : 'Economy'));
                  
                  const cabinFares = flight.fare_options ? flight.fare_options.filter(fo => fo.cabin_class === activeCabin) : [];
                  const selectedFareOption = cabinFares.length > 0 ? cabinFares[0] : null;
                  
                  const displayPrice = selectedFareOption ? selectedFareOption.price : (flight.price || 0);
                  const displayCurrency = selectedFareOption ? selectedFareOption.currency : (flight.currency || 'LKR');
                  
                  const currentPriceBreakdown = selectedFareOption ? selectedFareOption.price_breakdown : (flight.price_breakdown || {});
                  const bdKeys = currentPriceBreakdown ? Object.keys(currentPriceBreakdown) : [];
                  const grandTotal = bdKeys.length > 0
                    ? bdKeys.reduce((sum, ptc) => sum + (currentPriceBreakdown[ptc].total_price || 0), 0)
                    : displayPrice * totalPax;
                  const hasBreakdown = bdKeys.length > 0;
                  return (
                    <div key={idx} className="result-card animate-fade" style={{ flexDirection: 'column' }}>
                      <div className="rc-top-row" style={{ display: 'flex', alignItems: 'stretch', width: '100%', flex: 1 }}>
                        <div className="rc-airline-col">
                          {/* Airline Logo: use Travelport URL if provided, else Google Flights CDN by IATA code */}
                          <div className="rc-airline-logo" style={{ background: 'white', border: '1px solid #e2e8f0', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                            <img
                              src={flight.airline_logo_url || `https://www.gstatic.com/flights/airline_logos/70px/${flight.airline_code}.png`}
                              alt={flight.airline}
                              style={{ width: '44px', height: '44px', objectFit: 'contain' }}
                              onError={e => {
                                e.target.style.display = 'none';
                                e.target.nextSibling.style.display = 'flex';
                              }}
                            />
                            {/* Text fallback shown only if image fails */}
                            <span style={{ display: 'none', fontSize: '0.75rem', fontWeight: '800', color: 'var(--gs-crimson)', alignItems: 'center', justifyContent: 'center', width: '100%', height: '100%' }}>
                              {flight.airline_code || flight.airline?.slice(0, 2)?.toUpperCase() || 'FL'}
                            </span>
                          </div>
                          <div className="rc-airline-name">{flight.airline}</div>
                          {/* IATA airline code badge — from Travelport */}
                          <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center', marginTop: '0.15rem' }}>
                            <div style={{ fontSize: '0.65rem', fontWeight: '700', color: '#64748b', background: '#f1f5f9', borderRadius: '3px', padding: '0.1rem 0.35rem', letterSpacing: '0.03em' }}>
                              {flight.airline_code}
                            </div>
                            {/* Fare Source Badge: GDS / NDC / LCC */}
                            <div style={{
                              fontSize: '0.65rem', fontWeight: '800',
                              background: flight.fare_source === 'NDC' ? '#f5d0fe' : flight.fare_source === 'LCC' ? '#fef08a' : '#bae6fd',
                              color: flight.fare_source === 'NDC' ? '#701a75' : flight.fare_source === 'LCC' ? '#713f12' : '#0369a1',
                              borderRadius: '3px', padding: '0.1rem 0.4rem', letterSpacing: '0.04em', textTransform: 'uppercase'
                            }}>
                              {flight.fare_source || 'GDS'}
                            </div>
                          </div>
                          <div className="rc-flight-num">{flight.flight_number}</div>
                          {/* Aircraft type from Travelport equipment field */}
                          {flight.aircraft_type && (
                            <div style={{ fontSize: '0.62rem', color: '#94a3b8', marginTop: '0.2rem', fontWeight: '500' }}>
                              ✈ {flight.aircraft_type}
                            </div>
                          )}
                          {flight.stops > 0 && <div className="rc-stops-badge">{flight.stops} Stop{flight.stops > 1 ? 's' : ''}</div>}
                          {flight.stops === 0 && <div className="rc-nonstop-badge">Non-stop</div>}
                          {/* Seat-map availability badge — heuristic from carrier code Travelport returned.
                              Definitive check is always the live Travelport /api/bookings/initiate call. */}
                          {flight.seat_map_heuristic === true ? (
                            <div title="This airline typically supports seat selection via Travelport GDS. The actual seat map will be confirmed when you proceed to booking." style={{
                              display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
                              marginTop: '0.4rem', padding: '0.2rem 0.55rem',
                              borderRadius: '4px', fontSize: '0.6rem', fontWeight: '700',
                              background: '#f0fdf4', border: '1px solid #86efac', color: '#15803d',
                              letterSpacing: '0.02em', maxWidth: '100%', flexWrap: 'wrap'
                            }}>
                              <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><polyline points="20 6 9 17 4 12"/></svg>
                              Seat Selection Available
                            </div>
                          ) : (
                            <div title="Seat selection via Travelport GDS is not available for this carrier." style={{
                              display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
                              marginTop: '0.4rem', padding: '0.2rem 0.55rem',
                              borderRadius: '4px', fontSize: '0.6rem', fontWeight: '600',
                              background: '#f8fafc', border: '1px solid #cbd5e1', color: '#64748b',
                              letterSpacing: '0.02em', maxWidth: '100%', flexWrap: 'wrap'
                            }}>
                              <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                              Seat Selection Not Available
                            </div>
                          )}
                        </div>

                        {flight.legs && flight.legs.length === 2 ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.1rem', flex: 1, paddingTop: '0.9rem' }}>
                            <div style={{ position: 'relative' }}>
                              <ItineraryRow leg={flight.legs[0]} label="Outbound" />
                            </div>
                            <div style={{ position: 'relative', borderTop: '1px dashed #e2e8f0', paddingTop: '1.5rem' }}>
                              <ItineraryRow leg={flight.legs[1]} label="Return" />
                            </div>
                          </div>
                        ) : (
                          <ItineraryRow leg={flight} />
                        )}

                        <div className="rc-details-col" style={{ cursor: 'pointer' }} onClick={() => {
                          const fId = flight.offer_id || idx;
                          setExpandedFareIds(prev => ({ ...prev, [fId]: !prev[fId] }));
                        }}>
                          <div className="rc-cabin-tag" style={{ background: '#f1f5f9', color: '#1e293b', fontWeight: '800' }}>
                            {activeCabin === 'PremiumEconomy' ? 'Premium Economy' : activeCabin} Class
                          </div>
                          {flight.seats_remaining != null && (
                            <div className="rc-seats" style={{ color: flight.seats_remaining < 5 ? '#c3122e' : '#0f766e', fontWeight: '700', marginTop: '4px' }}>
                              {flight.seats_remaining < 5 ? `Only ${flight.seats_remaining} left` : `${flight.seats_remaining} seats`}
                            </div>
                          )}
                        </div>

                        <div className="rc-price-col">
                          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.5rem' }}>
                            <div style={{ textAlign: 'right' }}>
                              <div style={{ fontSize: '0.72rem', color: '#64748b', fontWeight: '600' }}>
                                from {displayCurrency}
                              </div>
                              <div style={{ fontSize: '1.35rem', fontWeight: '800', color: '#245d38', lineHeight: '1.1' }}>
                                {Math.floor(displayPrice).toLocaleString()}
                              </div>
                              <div style={{ fontSize: '0.65rem', color: '#245d38', fontWeight: '700', marginTop: '2px' }}>
                                Lowest price
                              </div>
                            </div>
                            {selectedFareOption && (
                              <button
                                type="button"
                                onClick={() => handleSelectFlight(flight, selectedFareOption)}
                                style={{
                                  background: '#245d38',
                                  color: 'white',
                                  border: 'none',
                                  borderRadius: '6px',
                                  padding: '0.5rem 1rem',
                                  fontWeight: '700',
                                  fontSize: '0.8rem',
                                  cursor: 'pointer',
                                  whiteSpace: 'nowrap',
                                  outline: 'none'
                                }}
                                onMouseEnter={e => e.target.style.opacity = 0.9}
                                onMouseLeave={e => e.target.style.opacity = 1}
                              >
                                Select Flight
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => {
                                const fId = flight.offer_id || idx;
                                setExpandedFareIds(prev => ({ ...prev, [fId]: !prev[fId] }));
                              }}
                              style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: '0.25rem',
                                background: 'none',
                                border: 'none',
                                color: '#64748b',
                                fontSize: '0.68rem',
                                fontWeight: '600',
                                cursor: 'pointer',
                                padding: '0.1rem',
                                outline: 'none'
                              }}
                            >
                              {isFareExpanded ? 'Hide other fares' : 'Other fares & cabins'}
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" style={{ transition: 'transform 0.2s', transform: isFareExpanded ? 'rotate(180deg)' : 'none' }}>
                                <path d="M6 9l6 6 6-6" />
                              </svg>
                            </button>
                          </div>
                        </div>
                      </div>

                      {/* ── Flight Segment Timeline — always visible, no toggle ── */}
                      <div style={{ borderTop: '1px solid #e2e8f0', background: '#fafbfc', padding: '1.25rem', width: '100%', boxSizing: 'border-box' }}>
                        {flight.legs && flight.legs.length === 2 ? (
                          ['Outbound', 'Return'].map((legLabel, lIdx) => {
                            const leg = flight.legs[lIdx];
                            if (!leg.segments || leg.segments.length === 0) return null;
                            return (
                              <div key={lIdx} style={{ marginBottom: lIdx === 0 ? '1.25rem' : 0 }}>
                                <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: '0.35rem', marginBottom: '1rem', borderBottom: '1px solid #e2e8f0', paddingBottom: '0.5rem' }}>
                                  <h4 style={{ margin: 0, fontSize: '0.82rem', color: '#1e293b', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{legLabel} — Flight Segment Timeline</h4>
                                  <span style={{ fontSize: '0.75rem', color: '#64748b', fontWeight: '600' }}>
                                    Total Duration: {leg.duration?.replace('PT','').replace('H','h ').replace('M','m')}
                                  </span>
                                </div>
                                <SegmentTimeline segments={leg.segments} />
                              </div>
                            );
                          })
                        ) : flight.segments && flight.segments.length > 0 ? (
                          <>
                            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: '0.35rem', marginBottom: '1rem', borderBottom: '1px solid #e2e8f0', paddingBottom: '0.5rem' }}>
                              <h4 style={{ margin: 0, fontSize: '0.82rem', color: '#1e293b', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Flight Segment Timeline</h4>
                              <span style={{ fontSize: '0.75rem', color: '#64748b', fontWeight: '600' }}>
                                Total Duration: {flight.duration?.replace('PT','').replace('H','h ').replace('M','m')}
                              </span>
                            </div>
                            <SegmentTimeline segments={flight.segments} />
                          </>
                        ) : null}
                      </div>

                      {/* ── Expandable Cabin Select and Fare Options ── */}
                      {isFareExpanded && (
                        <div style={{ borderTop: '1px solid #e2e8f0', background: '#ffffff', padding: '1.5rem', width: '100%', boxSizing: 'border-box' }}>
                          
                          {/* Segment Summary or Airline Details */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '1.25rem', color: '#64748b', fontSize: '0.78rem' }}>
                            <span style={{ fontWeight: '800', color: '#1e293b' }}>✈ {flight.aircraft_type || 'Boeing 777-300ER'}</span>
                            <span>•</span>
                            <span style={{ fontWeight: '700', color: '#475569' }}>{flight.airline_code}{flight.flight_number}</span>
                          </div>

                          {/* Cabin Selector Tabs */}
                          <div style={{ display: 'flex', borderBottom: '1.5px solid #e2e8f0', marginBottom: '1.5rem', gap: '2rem', boxSizing: 'border-box' }}>
                            {(() => {
                              const CABIN_ORDER = ['Economy', 'PremiumEconomy', 'Business', 'First'];
                              const allCabins = flight.fare_options ? Array.from(new Set(flight.fare_options.map(fo => fo.cabin_class))) : [];
                              const availableCabins = CABIN_ORDER.filter(c => allCabins.includes(c));
                              allCabins.forEach(c => {
                                if (!availableCabins.includes(c)) {
                                  availableCabins.push(c);
                                }
                              });
                              
                              return availableCabins.map((cab) => {
                                const isSelected = activeCabin === cab;
                                const fares = flight.fare_options.filter(fo => fo.cabin_class === cab);
                                const minPrice = fares.length > 0 ? fares[0].price : 0;
                                const label = cab === 'PremiumEconomy' ? 'Premium Economy' : cab;
                                const priceText = `from ${flight.currency} ${Math.floor(minPrice).toLocaleString()}`;

                                return (
                                  <button
                                    key={cab}
                                    type="button"
                                    onClick={() => {
                                      setSelectedCabins(prev => ({ ...prev, [fId]: cab }));
                                    }}
                                    style={{
                                      border: 'none',
                                      background: 'transparent',
                                      padding: '0.75rem 0',
                                      borderBottom: isSelected ? '3px solid #245d38' : '3px solid transparent',
                                      color: isSelected ? '#1e293b' : '#64748b',
                                      fontWeight: '700',
                                      fontSize: '0.9rem',
                                      cursor: 'pointer',
                                      display: 'flex',
                                      flexDirection: 'column',
                                      alignItems: 'flex-start',
                                      gap: '0.2rem',
                                      outline: 'none',
                                      transition: 'all 0.15s'
                                    }}
                                  >
                                    <span>{label}</span>
                                    <span style={{ fontSize: '0.75rem', fontWeight: '500', color: isSelected ? '#245d38' : '#94a3b8' }}>
                                      {priceText}
                                    </span>
                                  </button>
                                );
                              });
                            })()}
                          </div>

                          {/* Fare Options Container */}
                          <div style={{
                            display: 'grid',
                            gridTemplateColumns: `repeat(auto-fit, minmax(280px, 1fr))`,
                            gap: '1.25rem',
                            width: '100%',
                            boxSizing: 'border-box'
                          }}>
                            {cabinFares.map((fo, fIdx) => {
                              const isCheapest = fIdx === 0;
                              return (
                                <div key={fIdx} style={{
                                  display: 'flex',
                                  flexDirection: 'column',
                                  borderRadius: '10px',
                                  border: '1px solid #cbd5e1',
                                  overflow: 'hidden',
                                  background: '#ffffff',
                                  boxShadow: '0 4px 12px rgba(0,0,0,0.04)',
                                  transition: 'transform 0.15s, box-shadow 0.15s'
                                }}>
                                  {/* Card Header */}
                                  <div style={{
                                    background: isCheapest ? '#245d38' : '#1e293b',
                                    padding: '1.25rem',
                                    color: 'white',
                                    position: 'relative'
                                  }}>
                                    <div style={{ fontSize: '1rem', fontWeight: '800', letterSpacing: '0.01em' }}>{fo.brand_name}</div>
                                    <div style={{ fontSize: '1.35rem', fontWeight: '800', marginTop: '0.35rem' }}>
                                      {fo.currency} {Math.floor(fo.price).toLocaleString()}
                                    </div>
                                    {isCheapest && (
                                      <div style={{
                                        position: 'absolute',
                                        right: '12px',
                                        top: '12px',
                                        background: '#34d399',
                                        color: '#064e3b',
                                        fontSize: '0.58rem',
                                        fontWeight: '800',
                                        padding: '0.15rem 0.4rem',
                                        borderRadius: '3px',
                                        textTransform: 'uppercase',
                                        letterSpacing: '0.05em'
                                      }}>
                                        Cheapest
                                      </div>
                                    )}
                                    <div style={{ fontSize: '0.65rem', opacity: 0.9, marginTop: '0.25rem', fontWeight: '600', fontFamily: 'monospace' }}>
                                      Fare Basis: {(fo.fare_basis_codes || []).join(', ')} ({(fo.classes_of_service || []).join(', ')})
                                    </div>
                                  </div>

                                  {/* Card Body */}
                                  <div style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', flex: 1, gap: '1rem', justifyContent: 'space-between' }}>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                                      
                                      {/* Baggage Allowance Section */}
                                      {fo.baggage_allowance && fo.baggage_allowance.length > 0 && (
                                        <div style={{ borderBottom: '1px solid #f1f5f9', paddingBottom: '0.65rem' }}>
                                          <div style={{ fontSize: '0.65rem', fontWeight: '800', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '0.35rem' }}>Baggage Allowance</div>
                                          {fo.baggage_allowance.map((bag, bIdx) => (
                                            <div key={bIdx} style={{ fontSize: '0.78rem', color: '#334155', fontWeight: '600', display: 'flex', alignItems: 'center', gap: '0.35rem', marginTop: '0.2rem' }}>
                                              <span>🧳</span>
                                              <span>{bag.type}: <strong>{bag.allowance}</strong></span>
                                            </div>
                                          ))}
                                        </div>
                                      )}

                                      {/* Policies Section */}
                                      <div style={{ borderBottom: '1px solid #f1f5f9', paddingBottom: '0.65rem' }}>
                                        <div style={{ fontSize: '0.65rem', fontWeight: '800', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '0.35rem' }}>Rules & Policies</div>
                                        <div style={{ fontSize: '0.75rem', color: '#475569', display: 'flex', alignItems: 'flex-start', gap: '0.35rem', marginTop: '0.2rem' }}>
                                          <span style={{ fontSize: '0.8rem' }}>🔄</span>
                                          <div>
                                            <strong style={{ color: '#1e293b' }}>Changes:</strong> <span style={{ fontWeight: '500' }}>{fo.change_policy}</span>
                                          </div>
                                        </div>
                                        <div style={{ fontSize: '0.75rem', color: '#475569', display: 'flex', alignItems: 'flex-start', gap: '0.35rem', marginTop: '0.35rem' }}>
                                          <span style={{ fontSize: '0.8rem' }}>❌</span>
                                          <div>
                                            <strong style={{ color: '#1e293b' }}>Cancellations:</strong> <span style={{ fontWeight: '500' }}>{fo.cancel_policy}</span>
                                          </div>
                                        </div>
                                      </div>

                                      {/* Brand Attributes Section */}
                                      {fo.brand_attributes && fo.brand_attributes.length > 0 && (
                                        <div>
                                          <div style={{ fontSize: '0.65rem', fontWeight: '800', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '0.35rem' }}>Fare Benefits</div>
                                          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                                            {fo.brand_attributes.map((attr, aIdx) => {
                                              const labelMap = {
                                                'CheckedBag': 'Checked baggage allowance',
                                                'CarryOn': 'Cabin baggage allowance',
                                                'WiFi': 'On-board Wi-Fi access',
                                                'Meals': 'Meals & beverage service',
                                                'SeatAssignment': 'Standard seat selection',
                                                'PremiumSeat': 'Premium/preferred seating',
                                                'Refund': 'Ticket refund options',
                                                'Rebooking': 'Rebooking/flight change'
                                              };
                                              
                                              const displayLabel = labelMap[attr.classification] || attr.classification.replace(/([A-Z])/g, ' $1').trim();
                                              let icon = '✓';
                                              let color = '#16a34a';
                                              let bg = '#f0fdf4';
                                              let border = '#dcfce7';
                                              
                                              if (attr.inclusion === 'Chargeable') {
                                                icon = '$';
                                                color = '#d97706';
                                                bg = '#fffbeb';
                                                border = '#fef3c7';
                                              } else if (attr.inclusion === 'Not Offered') {
                                                icon = '✗';
                                                color = '#dc2626';
                                                bg = '#fef2f2';
                                                border = '#fee2e2';
                                              }
                                              
                                              return (
                                                <div key={aIdx} style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                                                  <span style={{
                                                    display: 'inline-flex',
                                                    alignItems: 'center',
                                                    justifyContent: 'center',
                                                    width: '14px',
                                                    height: '14px',
                                                    borderRadius: '50%',
                                                    background: bg,
                                                    border: `1px solid ${border}`,
                                                    color: color,
                                                    fontSize: '0.62rem',
                                                    fontWeight: '800',
                                                    flexShrink: 0
                                                  }}>{icon}</span>
                                                  <span style={{ fontSize: '0.75rem', color: '#475569', fontWeight: '500' }}>
                                                    {displayLabel}: <strong style={{ color: color }}>{attr.inclusion}</strong>
                                                  </span>
                                                </div>
                                              );
                                            })}
                                          </div>
                                        </div>
                                      )}

                                    </div>

                                    {/* Action Button */}
                                    <button
                                      type="button"
                                      onClick={() => handleSelectFlight(flight, fo)}
                                      style={{
                                        background: isCheapest ? '#245d38' : '#1e293b',
                                        color: 'white',
                                        border: 'none',
                                        borderRadius: '6px',
                                        padding: '0.65rem 1rem',
                                        fontWeight: '700',
                                        fontSize: '0.82rem',
                                        cursor: 'pointer',
                                        width: '100%',
                                        transition: 'background 0.2s',
                                        outline: 'none',
                                        marginTop: '1rem'
                                      }}
                                      onMouseEnter={e => e.target.style.opacity = 0.9}
                                      onMouseLeave={e => e.target.style.opacity = 1}
                                    >
                                      Select {fo.brand_name}
                                    </button>
                                  </div>
                                </div>
                              );
                            })}
                          </div>

                        </div>
                      )}

                      {/* ── Always-visible Per-Passenger Price Strip ── */}
                      {totalPax > 1 && hasBreakdown && (
                        <div style={{
                          width: '100%',
                          borderTop: '1px solid #e8ecf0',
                          background: 'linear-gradient(90deg, #f8fafc 0%, #fff5f6 100%)',
                          padding: '0.55rem 1.1rem',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '0',
                          flexWrap: 'wrap',
                          boxSizing: 'border-box',
                        }}>
                          {/* Label */}
                          <span style={{ fontSize: '0.67rem', fontWeight: '700', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.07em', marginRight: '0.85rem', whiteSpace: 'nowrap' }}>
                            Fare per type:
                          </span>

                          {/* Per-type pills */}
                          {Object.entries(currentPriceBreakdown).map(([ptc, details], pIdx) => {
                            const label = ptc === 'ADT' ? 'Adult' : ptc === 'CNN' ? 'Child (2–11)' : ptc === 'INF' ? 'Infant' : ptc;
                            const icon  = ptc === 'ADT' ? '🧑' : ptc === 'CNN' ? '🧒' : '👶';
                            const pillColors = [
                              { bg: '#eff6ff', border: '#bfdbfe', text: '#1d4ed8' },
                              { bg: '#f0fdf4', border: '#bbf7d0', text: '#15803d' },
                              { bg: '#fef9c3', border: '#fde68a', text: '#92400e' },
                            ];
                            const c = pillColors[pIdx % 3];
                            return (
                              <div key={ptc} style={{
                                display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
                                background: c.bg, border: `1px solid ${c.border}`, borderRadius: '20px',
                                padding: '0.22rem 0.65rem', marginRight: '0.5rem', marginBottom: '0.15rem',
                                fontSize: '0.72rem', fontWeight: '600', color: c.text,
                                whiteSpace: 'nowrap',
                              }}>
                                <span>{icon}</span>
                                <span>{label} ×{details.quantity}</span>
                                <span style={{ fontWeight: '800', marginLeft: '0.2rem' }}>
                                  {displayCurrency} {details.single_price?.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                </span>
                              </div>
                            );
                          })}

                          {/* Separator + Grand Total inline */}
                          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.4rem', whiteSpace: 'nowrap' }}>
                            <span style={{ fontSize: '0.67rem', color: '#94a3b8', fontWeight: '600' }}>Total:</span>
                            <span style={{ fontSize: '0.85rem', fontWeight: '800', color: 'var(--gs-crimson)' }}>
                              {displayCurrency} {grandTotal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                            </span>
                          </span>
                        </div>
                      )}

                      {/* ── Full Fare Breakdown Table ── */}
                      {hasBreakdown && totalPax > 1 && (
                        <div style={{ width: '100%', borderTop: '1px solid #e8ecf0', padding: '1rem 1.1rem', boxSizing: 'border-box' }}>
                            <div style={{ marginTop: 0, borderTop: 'none', paddingTop: 0 }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                                <h4 style={{ margin: 0, fontSize: '0.78rem', fontWeight: '700', color: '#1e293b', textTransform: 'uppercase', letterSpacing: '0.06em', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--gs-crimson)" strokeWidth="2.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
                                  Ticket Price Breakdown
                                </h4>
                                <span style={{ fontSize: '0.65rem', color: '#94a3b8', fontStyle: 'italic' }}>Source: Travelport GDS</span>
                              </div>

                              <div style={{ overflowX: 'auto' }}>
                              <div style={{ borderRadius: '8px', overflow: 'hidden', border: '1px solid #e2e8f0', minWidth: '480px' }}>
                                {/* Table header */}
                                <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr 1fr', padding: '0.4rem 0.85rem', background: '#1e293b', fontSize: '0.62rem', fontWeight: '700', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', gap: '0.5rem' }}>
                                  <span>Traveler</span>
                                  <span style={{ textAlign: 'right' }}>Qty</span>
                                  <span style={{ textAlign: 'right' }}>Base Fare</span>
                                  <span style={{ textAlign: 'right' }}>Taxes & Fees</span>
                                  <span style={{ textAlign: 'right' }}>Subtotal</span>
                                </div>

                                {Object.entries(flight.price_breakdown).map(([ptc, details], pIdx) => {
                                  const label = ptc === 'ADT' ? 'Adult' : ptc === 'CNN' ? 'Child (2–11 yrs)' : ptc === 'INF' ? 'Infant (<2 yrs)' : ptc;
                                  const icon  = ptc === 'ADT' ? '🧑' : ptc === 'CNN' ? '🧒' : '👶';
                                  return (
                                    <div key={ptc} style={{
                                      display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr 1fr',
                                      padding: '0.55rem 0.85rem', gap: '0.5rem',
                                      background: pIdx % 2 === 0 ? '#ffffff' : '#f8fafc',
                                      borderTop: pIdx === 0 ? 'none' : '1px solid #f1f5f9',
                                      fontSize: '0.75rem', color: '#334155', alignItems: 'center',
                                    }}>
                                      <span style={{ fontWeight: '700', color: '#1e293b', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                                        {icon} {label}
                                      </span>
                                      <span style={{ textAlign: 'right', color: '#64748b' }}>×{details.quantity}</span>
                                      <span style={{ textAlign: 'right' }}>{flight.currency} {details.base_price?.toFixed(2)}</span>
                                      <span style={{ textAlign: 'right', color: '#64748b' }}>{flight.currency} {(details.taxes + details.fees)?.toFixed(2)}</span>
                                      <span style={{ textAlign: 'right', fontWeight: '700', color: '#1e293b' }}>{flight.currency} {details.total_price?.toFixed(2)}</span>
                                    </div>
                                  );
                                })}

                                {/* Grand total row */}
                                <div style={{
                                  display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr 1fr',
                                  padding: '0.6rem 0.85rem', gap: '0.5rem',
                                  background: 'rgba(195,18,46,0.05)', borderTop: '2px solid var(--gs-crimson)',
                                  fontSize: '0.8rem', fontWeight: '800', color: 'var(--gs-crimson)', alignItems: 'center',
                                }}>
                                  <span style={{ gridColumn: '1 / 5' }}>Total for All {totalPax} Passengers</span>
                                  <span style={{ textAlign: 'right', fontSize: '0.95rem' }}>{flight.currency} {grandTotal.toFixed(2)}</span>
                                </div>
                              </div>
                              </div>
                            </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </main>
            </div>
          </div>
        );
      })()}
      {/* ── MY BOOKINGS TAB ──────────────────────────────────────────────── */}
      {/* ── INVOICE DATA TAB ─────────────────────────────────────────────── */}
      {activeTab === 'invoice' && (
        <div className="animate-fade" style={{ maxWidth: '1100px', margin: '2rem auto', padding: '0 1.5rem' }}>

          {/* Header */}
          <div style={{ marginBottom: '1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem' }}>
            <div>
              <h2 style={{ fontSize: '1.5rem', fontWeight: '800', color: 'var(--gs-dark)', margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--gs-crimson)" strokeWidth="2.5"><rect x="5" y="2" width="14" height="20" rx="2"/><path d="M9 7h6M9 11h6M9 15h4"/></svg>
                Invoice & PNR Reporting
              </h2>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '0.35rem 0 0 0' }}>
                Retrieve real-time booking details or generate date-wise ticketing reports directly from the Travelport GDS. No mock data.
              </p>
            </div>

            {/* Sub-tab Toggle */}
            <div style={{ display: 'flex', background: '#f1f5f9', borderRadius: '8px', padding: '0.2rem', gap: '0.2rem' }}>
              <button onClick={() => setInvoiceMode('single')} style={{
                border: 'none', background: invoiceMode === 'single' ? 'white' : 'transparent',
                color: invoiceMode === 'single' ? '#0f172a' : '#64748b', fontSize: '0.75rem', fontWeight: '700',
                padding: '0.45rem 1rem', borderRadius: '6px', cursor: 'pointer', transition: 'all 0.1s'
              }}>Single PNR Retrieve</button>
              <button onClick={() => setInvoiceMode('report')} style={{
                border: 'none', background: invoiceMode === 'report' ? 'white' : 'transparent',
                color: invoiceMode === 'report' ? '#0f172a' : '#64748b', fontSize: '0.75rem', fontWeight: '700',
                padding: '0.45rem 1rem', borderRadius: '6px', cursor: 'pointer', transition: 'all 0.1s'
              }}>Date-wise Report (PCC 7F3C)</button>
            </div>
          </div>

          {/* Mode 1: Single PNR Lookup */}
          {invoiceMode === 'single' && (
            <div style={{ background: 'white', border: '1px solid #e2e8f0', borderRadius: '10px', padding: '1.25rem', marginBottom: '1.5rem', boxShadow: '0 1px 6px rgba(0,0,0,0.05)' }}>
              <label style={{ fontSize: '0.78rem', fontWeight: '700', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '0.5rem' }}>Enter PNR / Booking Reference</label>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', alignItems: 'center' }}>
                <input
                  id="invoice-pnr-input"
                  type="text"
                  className="form-input"
                  placeholder="e.g. ABC123"
                  value={invoicePnr}
                  onChange={e => setInvoicePnr(e.target.value.toUpperCase())}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && invoicePnr.trim().length >= 5) {
                      setInvoiceError('');
                      setInvoiceData(null);
                      setInvoiceLoading(true);
                      fetch(`${API_BASE}/invoice/pnr/${invoicePnr.trim()}`)
                        .then(r => { if (!r.ok) return r.json().then(d => { throw new Error(d.detail || r.statusText); }); return r.json(); })
                        .then(d => { setInvoiceData(d.invoice); setInvoiceLoading(false); })
                        .catch(err => { setInvoiceError(err.message); setInvoiceLoading(false); });
                    }
                  }}
                  style={{ flex: 1, maxWidth: '320px', textTransform: 'uppercase', letterSpacing: '0.1em', fontWeight: '700', fontSize: '1rem' }}
                />
                <button
                  id="invoice-retrieve-btn"
                  className="btn btn-primary"
                  disabled={invoicePnr.trim().length < 5 || invoiceLoading}
                  onClick={() => {
                    setInvoiceError('');
                    setInvoiceData(null);
                    setInvoiceLoading(true);
                    fetch(`${API_BASE}/invoice/pnr/${invoicePnr.trim()}`)
                      .then(r => { if (!r.ok) return r.json().then(d => { throw new Error(d.detail || r.statusText); }); return r.json(); })
                      .then(d => { setInvoiceData(d.invoice); setInvoiceLoading(false); })
                      .catch(err => { setInvoiceError(err.message); setInvoiceLoading(false); });
                  }}
                  style={{ padding: '0.65rem 1.5rem', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}
                >
                  {invoiceLoading ? (
                    <><span className="spinner" style={{ width: '14px', height: '14px', borderWidth: '2px' }}></span> Retrieving...</>
                  ) : (
                    <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg> Retrieve from Travelport</>
                  )}
                </button>
              </div>
              {invoiceError && (
                <div style={{ marginTop: '0.75rem', background: '#fff1f2', border: '1px solid #fecdd3', borderRadius: '6px', padding: '0.6rem 0.85rem', fontSize: '0.8rem', color: '#be123c', display: 'flex', alignItems: 'flex-start', gap: '0.4rem' }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ flexShrink: 0, marginTop: '1px' }}><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
                  {invoiceError}
                </div>
              )}
            </div>
          )}

          {/* Mode 2: Date-wise PNR Report */}
          {invoiceMode === 'report' && (
            <div style={{ background: 'white', border: '1px solid #e2e8f0', borderRadius: '10px', padding: '1.25rem', marginBottom: '1.5rem', boxShadow: '0 1px 6px rgba(0,0,0,0.05)' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem', alignItems: 'flex-end' }}>
                <div style={{ flex: 1, minWidth: '160px' }}>
                  <label style={{ fontSize: '0.75rem', fontWeight: '700', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '0.35rem' }}>Start Date</label>
                  <input type="date" className="form-input" value={reportStartDate} onChange={e => setReportStartDate(e.target.value)} style={{ width: '100%' }} />
                </div>
                <div style={{ flex: 1, minWidth: '160px' }}>
                  <label style={{ fontSize: '0.75rem', fontWeight: '700', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '0.35rem' }}>End Date</label>
                  <input type="date" className="form-input" value={reportEndDate} onChange={e => setReportEndDate(e.target.value)} style={{ width: '100%' }} />
                </div>
                <button
                  className="btn btn-primary"
                  disabled={reportLoading}
                  onClick={() => {
                    setReportError('');
                    setReportData([]);
                    setReportLoading(true);
                    fetch(`${API_BASE}/invoice/report?start_date=${reportStartDate}&end_date=${reportEndDate}`)
                      .then(r => { if (!r.ok) return r.json().then(d => { throw new Error(d.detail || r.statusText); }); return r.json(); })
                      .then(d => { setReportData(d.pax_records); setReportLoading(false); })
                      .catch(err => { setReportError(err.message); setReportLoading(false); });
                  }}
                  style={{ padding: '0.65rem 1.5rem', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: '0.4rem', height: '38px' }}
                >
                  {reportLoading ? (
                    <><span className="spinner" style={{ width: '14px', height: '14px', borderWidth: '2px' }}></span> Compiling Report...</>
                  ) : (
                    <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg> Fetch PCC Report (7F3C)</>
                  )}
                </button>
              </div>
              
              {reportError && (
                <div style={{ marginTop: '0.75rem', background: '#fff1f2', border: '1px solid #fecdd3', borderRadius: '6px', padding: '0.6rem 0.85rem', fontSize: '0.8rem', color: '#be123c', display: 'flex', alignItems: 'flex-start', gap: '0.4rem' }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ flexShrink: 0, marginTop: '1px' }}><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
                  {reportError}
                </div>
              )}
              {!reportLoading && !reportError && reportData.length === 0 && invoiceMode === 'report' && (
                <div style={{ marginTop: '1rem', background: '#f8fafc', border: '1.5px dashed #cbd5e1', borderRadius: '10px', padding: '1.5rem', textAlign: 'center' }}>
                  <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" strokeWidth="1.5" style={{ margin: '0 auto 0.75rem' }}><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><path d="M9 16l2 2 4-4"/></svg>
                  <p style={{ fontWeight: '700', color: '#475569', fontSize: '0.9rem', marginBottom: '0.35rem' }}>No bookings found in this date range</p>
                  <p style={{ fontSize: '0.78rem', color: '#94a3b8', lineHeight: '1.6' }}>
                    This report queries <strong>your local PCC 7F3C booking records</strong> from the Travelport database.<br/>
                    Only PNRs booked through this system appear here. Try a wider date range — all data is sourced live from Travelport, not generated locally.
                  </p>
                  <div style={{ marginTop: '0.85rem', display: 'flex', justifyContent: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <button className="btn btn-secondary" style={{ fontSize: '0.75rem', padding: '0.35rem 0.85rem' }} onClick={() => { const d = new Date(); d.setDate(d.getDate() - 30); setReportStartDate(d.toISOString().split('T')[0]); setReportEndDate(new Date().toISOString().split('T')[0]); }}>📅 Last 30 Days</button>
                    <button className="btn btn-secondary" style={{ fontSize: '0.75rem', padding: '0.35rem 0.85rem' }} onClick={() => { const d = new Date(); d.setDate(d.getDate() - 90); setReportStartDate(d.toISOString().split('T')[0]); setReportEndDate(new Date().toISOString().split('T')[0]); }}>📅 Last 90 Days</button>
                    <button className="btn btn-secondary" style={{ fontSize: '0.75rem', padding: '0.35rem 0.85rem' }} onClick={() => { setReportStartDate('2026-01-01'); setReportEndDate(new Date().toISOString().split('T')[0]); }}>📅 All of 2026</button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Results */}
          {invoiceData && (() => {
            const inv = invoiceData;
            const paxList = inv.all_passengers || [];
            const segs = inv.segments || [];
            const breakdown = inv.price_breakdown || {};
            const breakdownKeys = Object.keys(breakdown);
            // Grand total = Travelport flight fare + seat fee (seat prices from Travelport Seat Map API)
            // inv.grand_total is computed by backend: total_fare + seat_charge
            const flightFare = inv.total_fare || 0;
            const seatFee = inv.seat_charge || 0;
            const grandTotal = inv.grand_total || flightFare;

            // Export helpers
            const handleExportJSON = () => {
              const blob = new Blob([JSON.stringify(inv, null, 2)], { type: 'application/json' });
              const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
              a.download = `invoice_${inv.agency_pnr || invoicePnr}.json`; a.click();
            };

            const handleExportCSV = () => {
              const cur = inv.currency || '';
              const fs  = inv.fare_summary || {};

              // Collect ALL unique tax codes across all pax types (dynamic — varies by airline)
              const allTaxCodes = [];
              breakdownKeys.forEach(ptc => {
                (breakdown[ptc]?.individual_taxes || []).forEach(tx => {
                  if (!allTaxCodes.includes(tx.code)) allTaxCodes.push(tx.code);
                });
              });

              // Tax code label lookup
              const TAX_LABELS = { LK:'Airport Tax', YQ:'Fuel Surcharge', YR:'Carrier Surcharge', IN:'India Entry Tax', MY:'Malaysia Dep Tax', OO:'Malaysia Aviation Levy', MF:'Malaysia Tourism Tax', F6:'Pax Service Charge', D8:'Domestic Dep Tax', WO:'Welfare Cess', JN:'Service Tax', P2:'Pax Service Fee', SQ:'Security Tax', K3:'Education Cess', XT:'Combined Tax' };

              // ── SINGLE header row ──────────────────────────────────────────────────
              const header = [
                // Booking Info
                'PNR', 'Airline PNR', 'Status', 'Booking Date', 'Ticket No',
                'Flight No', 'Airline', 'From (IATA)', 'To (IATA)',
                'Departure Time', 'Arrival Time', 'Cabin Class', 'Seat Class', 'Fare Basis', 'Baggage',
                // Fare Summary (offer-level — Travelport Offer.Price)
                `Base Fare All Pax (${cur})`, `Total Taxes All Pax (${cur})`, `Total Fees (${cur})`,
                `Flight Fare Total (${cur})`, `Seat Selection Fee (${cur})`, `Grand Total (${cur})`, 'Currency',
                // Per-pax-type fare (Travelport PriceBreakdownAir — Charged LKR entry)
                'Pax Type', 'Pax Base Fare', 'Pax Total Taxes', 'Pax Total',
                // Dynamic individual tax columns (one per tax code)
                ...allTaxCodes.map(c => `${TAX_LABELS[c] || c} (${c})`),
                // Fare calculation
                'Fare Calculation (IATA)', 'Filed Amount (USD)', 'Data Source',
                // Passenger personal details
                'Pax Count', 'Adults', 'Children', 'Infants', 'Pax Label', 'Full Name', 'Passport No',
                'Passport Expiry', 'Date of Birth', 'Gender', 'Nationality', 'Email', 'Phone',
              ].map(h => `"${h}"`).join(',');

              // ── One data row per passenger ────────────────────────────────────────
              const dataRows = paxList.map(p => {
                const pb = breakdown[p.passenger_type] || {};
                const taxMap = {};
                (pb.individual_taxes || []).forEach(tx => { taxMap[tx.code] = tx.amount; });

                const isAdult = p.passenger_type === 'ADT' ? 1 : 0;
                const isChild = p.passenger_type === 'CNN' ? 1 : 0;
                const isInfant = p.passenger_type === 'INF' ? 1 : 0;

                return [
                  // Booking Info
                  inv.agency_pnr || '', inv.airline_pnr || '', inv.status || '',
                  inv.booking_date || '', inv.ticket_number || '',
                  inv.flight_number || '', inv.airline || '',
                  inv.departure_airport || '', inv.arrival_airport || '',
                  inv.departure_time || '', inv.arrival_time || '',
                  inv.cabin_class || '', inv.class_of_service || '', inv.fare_basis || '', inv.baggage_allowance || '',
                  // Fare Summary
                  Number(fs.base_fare_total||0).toFixed(2),
                  Number(fs.total_taxes||0).toFixed(2),
                  Number(fs.total_fees||0).toFixed(2),
                  flightFare.toFixed(2),
                  seatFee.toFixed(2),
                  grandTotal.toFixed(2),
                  cur,
                  // Per-pax type fare
                  p.passenger_type || '',
                  Number(pb.base_price||0).toFixed(2),
                  Number(pb.taxes_total||0).toFixed(2),
                  Number(pb.total_price||0).toFixed(2),
                  // Individual taxes (dynamic columns)
                  ...allTaxCodes.map(c => Number(taxMap[c] || 0).toFixed(2)),
                  // Fare calculation
                  pb.fare_calculation || '',
                  pb.filed_usd_base ? pb.filed_usd_base.toFixed(2) : '',
                  'Travelport Reservation API + PriceBreakdownAir (Charged LKR)',
                  // Passenger details
                  1, isAdult, isChild, isInfant, p.passenger_type_label || '', p.full_name || '',
                  p.passport_number || '', p.passport_expiry || '', p.date_of_birth || '',
                  p.gender || '', p.nationality || '', p.email || '', p.phone || '',
                ].map(v => `"${String(v).replace(/"/g, '""')}"`).join(',');
              });

              const csv = [header, ...dataRows].join('\n');
              const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' });
              const a = document.createElement('a');
              a.href = URL.createObjectURL(blob);
              a.download = `invoice_${inv.agency_pnr || invoicePnr}_tax_breakdown.csv`;
              a.click();
            };





            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

                {/* Source Badge + Export */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span style={{ background: '#dcfce7', color: '#15803d', fontSize: '0.7rem', fontWeight: '700', padding: '0.2rem 0.6rem', borderRadius: '20px', border: '1px solid #bbf7d0' }}>✓ LIVE TRAVELPORT DATA</span>
                    <span style={{ fontSize: '0.72rem', color: '#64748b' }}>Source: {inv.source}</span>
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button id="invoice-export-json" onClick={handleExportJSON} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', padding: '0.4rem 0.9rem', fontSize: '0.78rem', fontWeight: '700', background: '#1e293b', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer' }}>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                      JSON
                    </button>
                    <button id="invoice-export-csv" onClick={handleExportCSV} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', padding: '0.4rem 0.9rem', fontSize: '0.78rem', fontWeight: '700', background: '#0f766e', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer' }}>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                      CSV
                    </button>
                  </div>
                </div>

                {/* Booking Summary Card */}
                <div style={{ background: 'white', border: '1px solid #e2e8f0', borderRadius: '10px', overflow: 'hidden', boxShadow: '0 1px 6px rgba(0,0,0,0.05)' }}>
                  <div style={{ background: '#1e293b', padding: '0.75rem 1.25rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ color: 'white', fontWeight: '700', fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.06em' }}>📋 Booking Summary</span>
                    <span style={{ fontSize: '0.7rem', padding: '0.15rem 0.6rem', borderRadius: '20px', fontWeight: '700',
                      background: inv.status === 'Ticketed' ? '#dcfce7' : '#fef9c3',
                      color: inv.status === 'Ticketed' ? '#15803d' : '#92400e' }}>
                      {inv.status}
                    </span>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '1px', background: '#f1f5f9', borderTop: '1px solid #f1f5f9' }}>
                    {[['Agency PNR', inv.agency_pnr], ['Airline PNR', inv.airline_pnr ? `${inv.airline_pnr} (${inv.airline_pnr_source})` : '—'], ['Ticket No.', inv.ticket_number || '—'], ['Booking Date', inv.booking_date || '—'], ['Flight', inv.flight_number || '—'], ['Route', inv.departure_airport && inv.arrival_airport ? `${inv.departure_airport} → ${inv.arrival_airport}` : '—'], ['Cabin', inv.cabin_class || '—'], ['Seat Class', inv.class_of_service || '—'], ['Fare Basis', inv.fare_basis || '—']].map(([label, val], i) => (
                      <div key={i} style={{ padding: '0.75rem 1.25rem', background: 'white' }}>
                        <div style={{ fontSize: '0.65rem', color: '#94a3b8', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.2rem' }}>{label}</div>
                        <div style={{ fontSize: '0.85rem', fontWeight: '700', color: '#1e293b' }}>{val || '—'}</div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* All Passengers Table */}
                <div style={{ background: 'white', border: '1px solid #e2e8f0', borderRadius: '10px', overflow: 'hidden', boxShadow: '0 1px 6px rgba(0,0,0,0.05)' }}>
                  <div style={{ background: '#c3122e', padding: '0.75rem 1.25rem' }}>
                    <span style={{ color: 'white', fontWeight: '700', fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.06em' }}>👥 Passengers ({paxList.length}) — from Travelport Reservation.Traveler[]</span>
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                      <thead>
                        <tr style={{ background: '#f8fafc', borderBottom: '2px solid #e2e8f0' }}>
                          {['#', 'Type', 'Full Name', 'Passport No.', 'Passport Expiry', 'Date of Birth', 'Gender', 'Nationality', 'Email', 'Phone'].map(h => (
                            <th key={h} style={{ padding: '0.5rem 0.85rem', textAlign: 'left', fontSize: '0.65rem', fontWeight: '700', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', whiteSpace: 'nowrap' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {paxList.length > 0 ? paxList.map((p, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid #f1f5f9', background: i % 2 === 0 ? 'white' : '#fafafa' }}>
                            <td style={{ padding: '0.6rem 0.85rem', fontWeight: '700', color: '#64748b' }}>{p.passenger_index}</td>
                            <td style={{ padding: '0.6rem 0.85rem' }}>
                              <span style={{ padding: '0.15rem 0.5rem', borderRadius: '20px', fontSize: '0.68rem', fontWeight: '700',
                                background: p.passenger_type === 'ADT' ? '#fee2e2' : p.passenger_type === 'CNN' ? '#eff6ff' : '#f0fdf4',
                                color: p.passenger_type === 'ADT' ? '#c3122e' : p.passenger_type === 'CNN' ? '#1d4ed8' : '#15803d' }}>
                                {p.passenger_type_label}
                              </span>
                            </td>
                            <td style={{ padding: '0.6rem 0.85rem', fontWeight: '700', color: '#1e293b', whiteSpace: 'nowrap' }}>{p.full_name || '—'}</td>
                            <td style={{ padding: '0.6rem 0.85rem', fontFamily: 'monospace', color: '#334155' }}>{p.passport_number || '—'}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#334155' }}>{p.passport_expiry || '—'}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#334155' }}>{p.date_of_birth || '—'}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#334155' }}>{p.gender || '—'}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#334155' }}>{p.nationality || '—'}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#334155' }}>{p.email || '—'}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#334155', whiteSpace: 'nowrap' }}>{p.phone || '—'}</td>
                          </tr>
                        )) : (
                          <tr><td colSpan="10" style={{ padding: '1.25rem', textAlign: 'center', color: '#94a3b8', fontStyle: 'italic' }}>No traveler data returned by Travelport for this PNR</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Flight Segments */}
                <div style={{ background: 'white', border: '1px solid #e2e8f0', borderRadius: '10px', overflow: 'hidden', boxShadow: '0 1px 6px rgba(0,0,0,0.05)' }}>
                  <div style={{ background: '#0f172a', padding: '0.75rem 1.25rem' }}>
                    <span style={{ color: 'white', fontWeight: '700', fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.06em' }}>✈ Flight Segments ({segs.length})</span>
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                      <thead>
                        <tr style={{ background: '#f8fafc', borderBottom: '2px solid #e2e8f0' }}>
                          {['Seg', 'Flight', 'Carrier', 'From', 'To', 'Departure', 'Arrival', 'Duration', 'Layover'].map(h => (
                            <th key={h} style={{ padding: '0.5rem 0.85rem', textAlign: 'left', fontSize: '0.65rem', fontWeight: '700', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', whiteSpace: 'nowrap' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {segs.length > 0 ? segs.map((seg, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid #f1f5f9', background: i % 2 === 0 ? 'white' : '#fafafa' }}>
                            <td style={{ padding: '0.6rem 0.85rem', fontWeight: '700', color: '#64748b' }}>{i + 1}</td>
                            <td style={{ padding: '0.6rem 0.85rem', fontWeight: '700', color: '#c3122e', fontFamily: 'monospace' }}>{seg.flight_number}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#334155' }}>{seg.carrier_name || seg.carrier}</td>
                            <td style={{ padding: '0.6rem 0.85rem', fontWeight: '700', color: '#1e293b' }}>{seg.departure_airport}</td>
                            <td style={{ padding: '0.6rem 0.85rem', fontWeight: '700', color: '#1e293b' }}>{seg.arrival_airport}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#334155', whiteSpace: 'nowrap' }}>{seg.departure_time}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#334155', whiteSpace: 'nowrap' }}>{seg.arrival_time}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#334155' }}>{seg.duration?.replace('PT','').replace('H','h ').replace('M','m') || '—'}</td>
                            <td style={{ padding: '0.6rem 0.85rem', color: '#b45309', fontWeight: '600', whiteSpace: 'nowrap' }}>{seg.layover_minutes > 0 ? `${Math.floor(seg.layover_minutes / 60)}h ${seg.layover_minutes % 60}m` : '—'}</td>
                          </tr>
                        )) : (
                          <tr><td colSpan="9" style={{ padding: '1.25rem', textAlign: 'center', color: '#94a3b8', fontStyle: 'italic' }}>No segment data in Travelport response</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* ── Fare Summary (Offer level from Travelport PriceDetail) ── */}
                {inv.fare_summary && (
                  <div style={{ background: 'white', border: '1px solid #e2e8f0', borderRadius: '10px', overflow: 'hidden', boxShadow: '0 1px 6px rgba(0,0,0,0.05)' }}>
                    <div style={{ background: '#0f766e', padding: '0.75rem 1.25rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ color: 'white', fontWeight: '700', fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.06em' }}>📊 Fare Summary — Travelport Offer.Price</span>
                      <span style={{ fontSize: '0.65rem', color: '#99f6e4', fontStyle: 'italic' }}>Source: PriceDetail object</span>
                    </div>
                    <div className="invoice-stat-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderTop: '1px solid #f1f5f9' }}>
                      {[
                        ['Base Fare (All Pax)', inv.fare_summary.base_fare_total, '#1e293b'],
                        ['Total Taxes', inv.fare_summary.total_taxes, '#b45309'],
                        ['Total Fees', inv.fare_summary.total_fees, '#64748b'],
                        ['Grand Total', inv.fare_summary.grand_total, '#c3122e'],
                      ].map(([label, val, color], i) => (
                        <div key={i} style={{ padding: '0.85rem 1.25rem', borderRight: i < 3 ? '1px solid #f1f5f9' : 'none' }}>
                          <div style={{ fontSize: '0.62rem', color: '#94a3b8', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.3rem' }}>{label}</div>
                          <div style={{ fontSize: '1rem', fontWeight: '800', color }}>{inv.currency} {Number(val).toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* ── Per-Passenger Tax Breakdown (from Travelport PriceBreakdownAir) ── */}
                <div style={{ background: 'white', border: '1px solid #e2e8f0', borderRadius: '10px', overflow: 'hidden', boxShadow: '0 1px 6px rgba(0,0,0,0.05)' }}>
                  <div style={{ background: '#7c3aed', padding: '0.75rem 1.25rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ color: 'white', fontWeight: '700', fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.06em' }}>💰 Tax Breakdown by Passenger Type</span>
                    <span style={{ fontSize: '0.65rem', color: '#ddd6fe', fontStyle: 'italic' }}>Source: PriceBreakdownAir (LKR Charged)</span>
                  </div>

                  {breakdownKeys.length > 0 ? breakdownKeys.map((ptc, pi) => {
                    const b = breakdown[ptc];
                    const taxColors = { LK: '#b45309', YQ: '#dc2626', YR: '#7c3aed', IN: '#0f766e' };
                    const rowBg = pi % 2 === 0 ? '#fafafa' : 'white';
                    return (
                      <div key={ptc} style={{ borderBottom: pi < breakdownKeys.length - 1 ? '2px solid #e2e8f0' : 'none' }}>
                        {/* Pax type header row */}
                        <div className="tax-breakdown-header-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', background: '#f8fafc', padding: '0.55rem 1rem', borderBottom: '1px solid #e2e8f0' }}>
                          <div>
                            <span style={{
                              padding: '0.18rem 0.55rem', borderRadius: '20px', fontSize: '0.7rem', fontWeight: '700',
                              background: ptc === 'ADT' ? '#fee2e2' : ptc === 'CNN' ? '#eff6ff' : '#f0fdf4',
                              color: ptc === 'ADT' ? '#c3122e' : ptc === 'CNN' ? '#1d4ed8' : '#15803d'
                            }}>{b.passenger_label || ptc}</span>
                          </div>
                          <div style={{ textAlign: 'right', fontSize: '0.78rem' }}>
                            <span style={{ color: '#64748b', fontSize: '0.65rem' }}>Base Fare</span><br />
                            <strong style={{ color: '#1e293b' }}>{inv.currency} {Number(b.base_price).toLocaleString(undefined, { minimumFractionDigits: 2 })}</strong>
                          </div>
                          <div style={{ textAlign: 'right', fontSize: '0.78rem' }}>
                            <span style={{ color: '#64748b', fontSize: '0.65rem' }}>Total Taxes</span><br />
                            <strong style={{ color: '#b45309' }}>{inv.currency} {Number(b.taxes_total).toLocaleString(undefined, { minimumFractionDigits: 2 })}</strong>
                          </div>
                          <div style={{ textAlign: 'right', fontSize: '0.78rem' }}>
                            <span style={{ color: '#64748b', fontSize: '0.65rem' }}>Total per Pax</span><br />
                            <strong style={{ color: '#c3122e', fontSize: '0.9rem' }}>{inv.currency} {Number(b.total_price).toLocaleString(undefined, { minimumFractionDigits: 2 })}</strong>
                          </div>
                        </div>

                        {/* Individual tax rows */}
                        {b.individual_taxes && b.individual_taxes.length > 0 && (
                          <div style={{ padding: '0.4rem 1rem 0.5rem', background: rowBg }}>
                            <div style={{ fontSize: '0.62rem', color: '#94a3b8', fontWeight: '700', textTransform: 'uppercase', marginBottom: '0.3rem' }}>Tax Detail (from Travelport Taxes.Tax[])</div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
                              {b.individual_taxes.map((tx, ti) => (
                                <div key={ti} style={{
                                  display: 'flex', alignItems: 'center', gap: '0.35rem',
                                  background: 'white', border: `1px solid ${taxColors[tx.code] || '#e2e8f0'}`,
                                  borderRadius: '6px', padding: '0.3rem 0.6rem', fontSize: '0.75rem'
                                }}>
                                  <span style={{
                                    background: taxColors[tx.code] || '#64748b', color: 'white',
                                    padding: '0.1rem 0.35rem', borderRadius: '4px', fontSize: '0.65rem', fontWeight: '800', fontFamily: 'monospace'
                                  }}>{tx.code}</span>
                                  <span style={{ color: '#475569' }}>{tx.description}</span>
                                  <span style={{ fontWeight: '700', color: taxColors[tx.code] || '#1e293b' }}>
                                    {tx.currency} {Number(tx.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Fare Calculation string */}
                        {b.fare_calculation && (
                          <div style={{ padding: '0.3rem 1rem 0.5rem', background: rowBg, borderTop: '1px dashed #f1f5f9' }}>
                            <span style={{ fontSize: '0.62rem', color: '#94a3b8', fontWeight: '700', textTransform: 'uppercase' }}>Fare Calculation: </span>
                            <code style={{ fontSize: '0.68rem', color: '#334155', background: '#f8fafc', padding: '0.1rem 0.4rem', borderRadius: '4px' }}>{b.fare_calculation}</code>
                            {b.filed_usd_base && (
                              <span style={{ marginLeft: '0.75rem', fontSize: '0.65rem', color: '#94a3b8' }}>Filed: USD {b.filed_usd_base?.toFixed(2)}</span>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  }) : (
                    <div style={{ padding: '1.25rem', textAlign: 'center', color: '#94a3b8', fontStyle: 'italic', fontSize: '0.8rem' }}>
                      Tax breakdown not returned by Travelport for this PNR
                    </div>
                  )}
                </div>

                {/* ── Invoice Totals + Commission/Incentive ── */}
                <div className="invoice-2col-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>

                  {/* Invoice Totals */}
                  <div style={{ background: 'white', border: '1px solid #e2e8f0', borderRadius: '10px', padding: '1.25rem', boxShadow: '0 1px 6px rgba(0,0,0,0.05)' }}>
                    <div style={{ fontSize: '0.7rem', fontWeight: '700', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.85rem' }}>Invoice Totals</div>

                    {/* Base Fare */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.82rem' }}>
                      <div>
                        <div style={{ color: '#475569' }}>✈ Base Fare (all passengers)</div>
                        <div style={{ fontSize: '0.62rem', color: '#94a3b8' }}>Travelport → Offer.Price.Base</div>
                      </div>
                      <strong style={{ color: '#1e293b' }}>{inv.currency} {Number(inv.fare_summary?.base_fare_total || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</strong>
                    </div>

                    {/* Total Taxes */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.82rem', paddingTop: '0.4rem', borderTop: '1px dashed #f1f5f9' }}>
                      <div>
                        <div style={{ color: '#475569' }}>🧾 Total Taxes</div>
                        <div style={{ fontSize: '0.62rem', color: '#94a3b8' }}>Travelport → Offer.Price.TotalTaxes</div>
                      </div>
                      <strong style={{ color: '#b45309' }}>{inv.currency} {Number(inv.fare_summary?.total_taxes || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</strong>
                    </div>

                    {/* Flight Fare Grand Total */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.82rem', paddingTop: '0.4rem', borderTop: '1px dashed #f1f5f9' }}>
                      <div>
                        <div style={{ color: '#475569', fontWeight: '600' }}>✈ Flight Fare Total</div>
                        <div style={{ fontSize: '0.62rem', color: '#94a3b8' }}>Travelport → Offer.Price.TotalPrice</div>
                      </div>
                      <strong style={{ color: '#1e293b' }}>{inv.currency} {flightFare.toLocaleString(undefined, { minimumFractionDigits: 2 })}</strong>
                    </div>

                    {/* Seat Fee */}
                    {seatFee > 0 && (
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.82rem', paddingTop: '0.4rem', borderTop: '1px dashed #f1f5f9' }}>
                        <div>
                          <div style={{ color: '#475569' }}>💺 Seat Selection Fee</div>
                          <div style={{ fontSize: '0.62rem', color: '#94a3b8' }}>Travelport Seat Map API → CatalogOfferings.Price</div>
                          <div style={{ fontSize: '0.6rem', color: '#b45309' }}>⚠ Not included in TotalPrice by Retrieve API</div>
                        </div>
                        <strong style={{ color: '#b45309' }}>{inv.currency} {seatFee.toLocaleString(undefined, { minimumFractionDigits: 2 })}</strong>
                      </div>
                    )}

                    {/* Baggage */}
                    {inv.baggage_allowance && (
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.82rem', paddingTop: '0.4rem', borderTop: '1px dashed #f1f5f9' }}>
                        <span style={{ color: '#475569' }}>🧳 Baggage Allowance</span>
                        <span style={{ color: '#64748b', fontWeight: '600' }}>{inv.baggage_allowance}</span>
                      </div>
                    )}

                    {/* Grand Total */}
                    <div style={{ borderTop: '2px solid #c3122e', paddingTop: '0.65rem', marginTop: '0.65rem', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div>
                        <div style={{ fontWeight: '800', fontSize: '0.95rem', color: '#1e293b' }}>Grand Total</div>
                        {seatFee > 0 && (
                          <div style={{ fontSize: '0.62rem', color: '#64748b', marginTop: '2px' }}>
                            {inv.currency} {flightFare.toLocaleString(undefined,{minimumFractionDigits:2})} (fare)
                            + {inv.currency} {seatFee.toLocaleString(undefined,{minimumFractionDigits:2})} (seats)
                          </div>
                        )}
                      </div>
                      <strong style={{ fontSize: '1.5rem', color: '#c3122e' }}>{inv.currency} {grandTotal.toLocaleString(undefined, { minimumFractionDigits: 2 })}</strong>
                    </div>
                  </div>

                  {/* Commission & Agent Incentive Panel */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>

                    {/* Commission Transparency */}
                    <div style={{ background: 'white', border: '1px solid #e2e8f0', borderRadius: '10px', overflow: 'hidden', boxShadow: '0 1px 6px rgba(0,0,0,0.05)' }}>
                      <div style={{ background: '#1e40af', padding: '0.7rem 1.1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ color: 'white', fontWeight: '700', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>💼 Commission & Agent Incentive</span>
                        <span style={{ fontSize: '0.62rem', color: '#bfdbfe', fontStyle: 'italic' }}>Travelport API Status</span>
                      </div>
                      <div style={{ padding: '1rem 1.1rem' }}>
                        {/* Status rows */}
                        {[
                          { label: 'Commission', icon: '💰', available: false, note: 'Not in Reservation Retrieve API response' },
                          { label: 'Agent Incentive / Override', icon: '🎁', available: false, note: 'Not in Reservation Retrieve API response' },
                          { label: 'Net Fare', icon: '📉', available: false, note: 'Not returned — only TotalPrice (gross) available' },
                          { label: 'Discount Amount', icon: '🏷', available: false, note: 'Not in Offer object for this PNR' },
                        ].map(({ label, icon, available, note }, i) => (
                          <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '0.6rem', padding: '0.4rem 0', borderBottom: '1px dashed #f1f5f9' }}>
                            <span style={{ fontSize: '0.9rem' }}>{icon}</span>
                            <div style={{ flex: 1 }}>
                              <div style={{ fontSize: '0.78rem', fontWeight: '700', color: '#1e293b' }}>{label}</div>
                              <div style={{ fontSize: '0.65rem', color: '#94a3b8' }}>{note}</div>
                            </div>
                            <span style={{
                              fontSize: '0.65rem', fontWeight: '700', padding: '0.15rem 0.5rem', borderRadius: '20px',
                              background: available ? '#dcfce7' : '#fee2e2',
                              color: available ? '#15803d' : '#dc2626'
                            }}>{available ? '✓ Available' : '✗ Not returned'}</span>
                          </div>
                        ))}
                        <div style={{ marginTop: '0.75rem', fontSize: '0.7rem', color: '#64748b', lineHeight: '1.5', background: '#f8fafc', borderRadius: '6px', padding: '0.6rem 0.75rem' }}>
                          <strong style={{ color: '#1e40af' }}>Why not available?</strong><br />
                          Travelport's <code style={{ background: '#e2e8f0', padding: '0 3px', borderRadius: '3px', fontSize: '0.65rem' }}>GET /reservations/{'{pnr}'}</code> returns only:
                          <code style={{ display: 'block', marginTop: '0.3rem', background: '#e2e8f0', padding: '0.3rem 0.5rem', borderRadius: '4px', fontSize: '0.65rem', color: '#334155' }}>
                            Product, Price, TermsAndConditionsFull
                          </code>
                          Commission & incentive data requires the <strong>Travelport Commission API</strong> or <strong>Queue/Ticketing workflow</strong>.
                        </div>
                      </div>
                    </div>

                    {/* Malaysian Airlines & Incentive Note */}
                    <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: '8px', padding: '0.85rem 1rem', fontSize: '0.73rem', color: '#1e40af' }}>
                      <div style={{ fontWeight: '700', marginBottom: '0.4rem', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                        ℹ️ MH & Other Airline Incentives
                      </div>
                      <p style={{ margin: 0, lineHeight: '1.55', color: '#334155' }}>
                        Airlines like <strong>Malaysia Airlines (MH)</strong>, <strong>SriLankan (UL)</strong>, and others offer travel agent
                        incentive/commission returns (e.g. BSP override, net fare deals). These are negotiated at the
                        <strong> BSP/IATA level</strong> and are <strong>not transmitted in the GDS reservation retrieve response</strong>.
                        They must be retrieved via your BSP statement, airline portal, or the{' '}
                        <strong>Travelport Commission API</strong> (<code style={{ background: '#dbeafe', padding: '0 3px', borderRadius: '3px' }}>POST /commission/air</code>).
                      </p>
                    </div>
                  </div>
                </div>

              </div>
            );
          })()}

          {/* Date-wise PNR Report Results */}
          {invoiceMode === 'report' && reportData.length > 0 && (() => {
            const allTaxCodes = [];
            reportData.forEach(r => {
              (r.individual_taxes || []).forEach(tx => {
                if (!allTaxCodes.includes(tx.code)) allTaxCodes.push(tx.code);
              });
            });

            const handleExportReportCSV = () => {
              const cur = reportData[0].currency || 'LKR';
              const TAX_LABELS = { LK:'Airport Tax', YQ:'Fuel Surcharge', YR:'Carrier Surcharge', IN:'India Entry Tax', MY:'Malaysia Dep Tax', OO:'Malaysia Aviation Levy', MF:'Malaysia Tourism Tax', F6:'Pax Service Charge', D8:'Domestic Dep Tax', WO:'Welfare Cess', JN:'Service Tax', P2:'Pax Service Fee', SQ:'Security Tax', K3:'Education Cess', XT:'Combined Tax' };

              const header = [
                'PNR', 'Airline PNR', 'Status', 'Booking Date', 'Payment Method', 'Fare Source', 'Ticket No',
                'Flight No', 'Airline', 'From (IATA)', 'To (IATA)',
                'Departure Time', 'Arrival Time', 'Cabin Class', 'Seat Class', 'Fare Basis', 'Baggage',
                'Base Fare All Pax', 'Total Taxes All Pax', 'Total Fees',
                'Flight Fare Total', 'Seat Selection Fee', 'Grand Total', 'Currency',
                'Pax Type', 'Pax Base Fare', 'Pax Total Taxes', 'Pax Total',
                ...allTaxCodes.map(c => `${TAX_LABELS[c] || c} (${c})`),
                'Fare Calculation (IATA)', 'Filed Amount (USD)', 'Data Source',
                // Passenger personal details
                'Pax Count', 'Adults', 'Children', 'Infants', 'Pax Label', 'Full Name', 'Passport No',
                'Passport Expiry', 'Date of Birth', 'Gender', 'Nationality', 'Email', 'Phone',
              ].map(h => `"${h}"`).join(',');

              const dataRows = reportData.map(r => {
                const taxMap = {};
                (r.individual_taxes || []).forEach(tx => { taxMap[tx.code] = tx.amount; });

                const isAdult = r.passenger_type === 'ADT' ? 1 : 0;
                const isChild = r.passenger_type === 'CNN' ? 1 : 0;
                const isInfant = r.passenger_type === 'INF' ? 1 : 0;

                return [
                  r.locator_code || '', r.airline_pnr || '', r.status || '', r.booking_date || '', r.payment_method || 'Credit Card', r.fare_source || 'GDS', r.ticket_number || '',
                  r.flight_number || '', r.airline || '', r.departure_airport || '', r.arrival_airport || '',
                  r.departure_time || '', r.arrival_time || '', r.cabin_class || '', r.class_of_service || '', r.fare_basis || '', r.baggage_allowance || '',
                  Number(r.base_fare_total||0).toFixed(2), Number(r.total_taxes||0).toFixed(2), '0.00',
                  Number(r.flight_fare||0).toFixed(2), Number(r.seat_charge||0).toFixed(2), Number(r.grand_total||0).toFixed(2), r.currency || 'LKR',
                  r.passenger_type || '', Number(r.pax_base_fare||0).toFixed(2), Number(r.pax_total_taxes||0).toFixed(2), Number(r.pax_total||0).toFixed(2),
                  ...allTaxCodes.map(c => Number(taxMap[c] || 0).toFixed(2)),
                  r.fare_calculation || '', r.filed_usd_base ? r.filed_usd_base.toFixed(2) : '', 'Travelport GDS Live Retrieve (PCC 7F3C)',
                  1, isAdult, isChild, isInfant, r.passenger_type_label || '', r.full_name || '', r.passport_number || '',
                  r.passport_expiry || '', r.date_of_birth || '', r.gender || '', r.nationality || '', r.email || '', r.phone || '',
                ].map(v => `"${String(v).replace(/"/g, '""')}"`).join(',');
              });

              const csv = [header, ...dataRows].join('\n');
              const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' });
              const a = document.createElement('a');
              a.href = URL.createObjectURL(blob);
              a.download = `datewise_report_${reportStartDate}_to_${reportEndDate}.csv`;
              a.click();
            };

            return (
              <div className="animate-fade" style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem', marginTop: '1rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
                  <span style={{ fontSize: '0.75rem', fontWeight: '700', padding: '0.25rem 0.75rem', background: '#e0f2fe', color: '#0369a1', borderRadius: '20px' }}>
                    🟢 Retrieved {reportData.length} Passenger Records Live from Travelport (PCC 7F3C)
                  </span>
                  <button onClick={handleExportReportCSV} className="btn btn-secondary" style={{ padding: '0.5rem 1rem', fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                    Export flat CSV
                  </button>
                </div>

                <div style={{ background: 'white', border: '1px solid #e2e8f0', borderRadius: '10px', overflow: 'hidden', boxShadow: '0 1px 6px rgba(0,0,0,0.05)' }}>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.74rem', minWidth: '950px' }}>
                      <thead>
                        <tr style={{ background: '#f8fafc', borderBottom: '2px solid #e2e8f0', textTransform: 'uppercase', fontSize: '0.62rem', fontWeight: '700', color: '#64748b' }}>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>PNR</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Txn Date</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Airline PNR</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Status</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Payment</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Source</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Pax Name</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Type</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Flight No</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>From/To</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Cabin</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Seat Cl.</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'left' }}>Ticket No.</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'right' }}>Base Fare</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'right' }}>Taxes</th>
                          {allTaxCodes.map(c => (
                            <th key={c} style={{ padding: '0.65rem 0.85rem', textAlign: 'right' }}>{c}</th>
                          ))}
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'right' }}>Seats</th>
                          <th style={{ padding: '0.65rem 0.85rem', textAlign: 'right' }}>Total</th>
                        </tr>
                      </thead>
                      <tbody>
                        {reportData.map((r, i) => {
                          const taxMap = {};
                          (r.individual_taxes || []).forEach(tx => { taxMap[tx.code] = tx.amount; });
                          return (
                            <tr key={i} style={{ borderBottom: '1px solid #f1f5f9', background: i % 2 === 0 ? 'white' : '#fafafa' }}>
                              <td style={{ padding: '0.6rem 0.85rem', fontWeight: '800', color: '#1e40af' }}>{r.locator_code}</td>
                              <td style={{ padding: '0.6rem 0.85rem', color: '#475569' }}>{r.booking_date ? r.booking_date.slice(0, 10) : '—'}</td>
                              <td style={{ padding: '0.6rem 0.85rem', fontWeight: '700', color: '#475569' }}>{r.airline_pnr || '—'}</td>
                              <td style={{ padding: '0.6rem 0.85rem' }}>
                                <span style={{
                                  fontSize: '0.65rem', fontWeight: '700', padding: '0.15rem 0.4rem', borderRadius: '4px',
                                  background: r.status === 'Ticketed' ? '#dcfce7' : r.status === 'Cancelled' ? '#fee2e2' : '#f1f5f9',
                                  color: r.status === 'Ticketed' ? '#15803d' : r.status === 'Cancelled' ? '#b91c1c' : '#475569'
                                }}>{r.status || 'Confirmed'}</span>
                              </td>
                              <td style={{ padding: '0.6rem 0.85rem', color: '#475569', fontWeight: '500' }}>{r.payment_method || 'Credit Card'}</td>
                              <td style={{ padding: '0.6rem 0.85rem' }}>
                                <span style={{
                                  fontSize: '0.65rem', fontWeight: '800', padding: '0.15rem 0.4rem', borderRadius: '4px',
                                  background: r.fare_source === 'NDC' ? '#f5d0fe' : r.fare_source === 'LCC' ? '#fef08a' : '#bae6fd',
                                  color: r.fare_source === 'NDC' ? '#701a75' : r.fare_source === 'LCC' ? '#713f12' : '#0369a1'
                                }}>{r.fare_source || 'GDS'}</span>
                              </td>
                              <td style={{ padding: '0.6rem 0.85rem', fontWeight: '700', color: '#1e293b' }}>{r.full_name}</td>
                              <td style={{ padding: '0.6rem 0.85rem', color: '#64748b' }}>{r.passenger_type_label}</td>
                              <td style={{ padding: '0.6rem 0.85rem', fontWeight: '600' }}>{r.flight_number}</td>
                              <td style={{ padding: '0.6rem 0.85rem' }}>{r.departure_airport} → {r.arrival_airport}</td>
                              <td style={{ padding: '0.6rem 0.85rem' }}>{r.cabin_class}</td>
                              <td style={{ padding: '0.6rem 0.85rem', fontWeight: '700', color: '#047857' }}>{r.class_of_service || '—'}</td>
                              <td style={{ padding: '0.6rem 0.85rem', fontFamily: 'monospace' }}>{r.ticket_number || '—'}</td>
                              <td style={{ padding: '0.6rem 0.85rem', textAlign: 'right' }}>{Number(r.pax_base_fare).toLocaleString(undefined, {minimumFractionDigits:2})}</td>
                              <td style={{ padding: '0.6rem 0.85rem', textAlign: 'right', color: '#64748b' }}>{Number(r.pax_total_taxes).toLocaleString(undefined, {minimumFractionDigits:2})}</td>
                              {allTaxCodes.map(c => (
                                <td key={c} style={{ padding: '0.6rem 0.85rem', textAlign: 'right', color: '#b45309' }}>
                                  {taxMap[c] ? Number(taxMap[c]).toLocaleString(undefined, {minimumFractionDigits:2}) : '0.00'}
                                </td>
                              ))}
                              <td style={{ padding: '0.6rem 0.85rem', textAlign: 'right', color: '#7c3aed' }}>
                                {r.seat_charge > 0 && r.passenger_index === 1 ? Number(r.seat_charge).toLocaleString(undefined, {minimumFractionDigits:2}) : '—'}
                              </td>
                              <td style={{ padding: '0.6rem 0.85rem', textAlign: 'right', fontWeight: '800', color: '#c3122e' }}>
                                {r.currency} {Number(r.pax_total + (r.passenger_index === 1 ? r.seat_charge : 0)).toLocaleString(undefined, {minimumFractionDigits:2})}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* Empty report state */}
          {invoiceMode === 'report' && !reportLoading && reportData.length === 0 && (
            <div style={{ background: '#f8fafc', border: '1px dashed #e2e8f0', borderRadius: '10px', padding: '3rem 1.5rem', textAlign: 'center', color: '#94a3b8', marginTop: '1rem' }}>
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ marginBottom: '0.75rem', opacity: 0.6 }}><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
              <div style={{ fontSize: '0.85rem', fontWeight: '600', color: '#64748b', marginBottom: '0.2rem' }}>No PNRs retrieved in selected range</div>
              <div style={{ fontSize: '0.75rem' }}>Select a date range and click "Fetch PCC Report" to query live Travelport records.</div>
            </div>
          )}

        </div>
      )}

      {activeTab === 'bookings' && (
        <div className="tab-content animate-fade">
          <section className="search-section glass-panel">
            <h2 className="section-title">My Boarding Passes</h2>
            <div className="form-group">
              <label className="form-label">Filter by Email Address</label>
              <div className="search-input-wrapper">
                <input type="email" className="form-input" placeholder="Enter email to filter bookings"
                  value={searchEmail} onChange={e => setSearchEmail(e.target.value)} />
                {searchEmail && <button className="clear-input-btn" onClick={() => { setSearchEmail(''); fetchBookings(''); }}>×</button>}
              </div>
            </div>
            <button className="btn btn-primary" onClick={() => fetchBookings()} style={{ marginTop: '0.5rem' }}>
              Load Bookings
            </button>
          </section>

          <section className="bookings-list-section">
            <h3 className="results-heading">
              {loadingBookings ? 'Loading...' : `Issued Tickets (${myBookings.length})`}
            </h3>
            {loadingBookings ? (
              <div className="loading-state"><div className="spinner"></div><p>Retrieving bookings...</p></div>
            ) : myBookings.length === 0 ? (
              <div className="empty-state glass-panel animate-fade">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M2 17h20M2 12h20M2 7h20"/></svg>
                <p>No bookings found. Book a flight to see your boarding passes here.</p>
              </div>
            ) : (
              <div className="bookings-grid">
                {myBookings.map((b) => (
                  <div key={b.id} className={`boarding-pass glass-panel ${b.status === 'Cancelled' ? 'cancelled-pass' : ''} animate-fade`}>
                    <div className="pass-row header-row">
                      <div className="pass-airline">
                        <span className="pass-airline-name">{b.airline}</span>
                        <span className="pass-flight-badge">{b.flight_number}</span>
                      </div>
                      <div className={`pass-status-badge ${b.status?.toLowerCase()}`}>{b.status}</div>
                    </div>
                    <div className="pass-row route-row">
                      <div className="route-endpoint"><span className="route-city">{b.departure_airport}</span><span className="route-label">DEPARTURE</span></div>
                      <div className="route-arrow"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg></div>
                      <div className="route-endpoint"><span className="route-city">{b.arrival_airport}</span><span className="route-label">ARRIVAL</span></div>
                    </div>
                    <div className="pass-details-grid">
                      <div className="detail-item"><span className="detail-label">PASSENGER</span><span className="detail-val">{cleanPassengerName(b.passenger_name)}</span></div>
                      <div className="detail-item"><span className="detail-label">PNR / LOCATOR</span><span className="detail-val highlight">{b.locator_code}</span></div>
                      <div className="detail-item"><span className="detail-label">TICKET NO</span><span className="detail-val">{b.ticket_number || '—'}</span></div>
                      <div className="detail-item"><span className="detail-label">DEPARTURE</span><span className="detail-val">{b.departure_time}</span></div>
                      <div className="detail-item"><span className="detail-label">CABIN CLASS</span><span className="detail-val">{b.cabin_class}</span></div>
                      <div className="detail-item"><span className="detail-label">FARE</span><span className="detail-val highlight">{b.currency} {b.total_fare?.toFixed(2)}</span></div>
                    </div>
                    <div className="pass-footer" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
                      <div className="pass-barcode-box" style={{ flex: 1 }}><span className="barcode-label">PNR: {b.locator_code}</span></div>
                      <div style={{ display: 'flex', gap: '0.4rem' }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => handleViewTicket(b)}>📋 View/Print Ticket</button>
                        {b.status !== 'Cancelled' && (
                          <button className="btn btn-danger btn-sm" onClick={() => handleCancelBooking(b.locator_code)}>Cancel Booking</button>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {/* ── BOOKING WIZARD MODAL ─────────────────────────────────────────── */}
      {selectedFlight && bookingStep && bookingStep !== 'ticket' && (
        <div className="modal-overlay animate-fade-only">
          <div className="modal-content glass-panel animate-fade" style={{ maxWidth: (bookingStep === 'seats' || bookingStep === 'seats_loading') ? '850px' : '640px', width: '95%', transition: 'max-width 0.25s ease' }}>
            {/* Progress Steps */}
            <div style={{ display: 'flex', gap: '0', padding: '1.25rem 1.5rem 1rem', borderBottom: '1px solid var(--border-color)', marginBottom: '0.25rem' }}>
  {(() => {
                const steps = [
                  { key: 'passenger', label: 'Passenger Details' },
                  { key: 'seats', label: 'Seat Selection' },
                  { key: 'review', label: 'Review Booking' },
                  { key: 'payment', label: 'Payment' }
                ];
                // When seat map is unavailable (skipped), mark the seats step as completed/skipped
                const seatStepSkipped = !seatMap && (bookingStep === 'review' || bookingStep === 'payment' || bookingStep === 'processing');
                return steps.map((step, idx) => {
                  const isActive = bookingStep === step.key ||
                                  (step.key === 'seats' && (bookingStep === 'seats_loading')) ||
                                  (step.key === 'payment' && bookingStep === 'processing');
                  const isSkipped = step.key === 'seats' && seatStepSkipped;
                  return (
                    <div key={step.key} style={{ flex: 1, textAlign: 'center', fontSize: '0.75rem', color: isSkipped ? '#94a3b8' : isActive ? 'var(--gs-crimson)' : 'var(--text-muted)', fontWeight: isActive ? '700' : '400', position: 'relative' }}>
                      <div style={{ width: '24px', height: '24px', borderRadius: '50%', background: isSkipped ? '#e2e8f0' : isActive ? 'var(--gs-crimson)' : 'var(--border-color)', color: isSkipped ? '#94a3b8' : 'white', margin: '0 auto 4px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', fontWeight: '700' }}>
                        {isSkipped ? '—' : idx + 1}
                      </div>
                      {step.label}
                      {isSkipped && <div style={{ fontSize: '0.6rem', color: '#94a3b8', marginTop: '1px' }}>N/A</div>}
                    </div>
                  );
                });
              })()}
            </div>

            <div className="modal-header">
              <h3>
                {bookingStep === 'passenger' ? 'Passenger Details' : 
                 bookingStep === 'seats_loading' ? 'Loading Seat Map...' :
                 bookingStep === 'seats' ? 'Select Passenger Seats' :
                 bookingStep === 'seats_unavailable' ? 'Seat Selection Unavailable' :
                 bookingStep === 'review' ? 'Review Your Booking' : 
                 bookingStep === 'payment' ? 'Payment Details' :
                 'Issuing Ticket...'}
              </h3>
              {bookingStep !== 'processing' && <button className="close-btn" onClick={closeBookingFlow}>×</button>}
            </div>

            {/* Flight Summary */}
            <div className="modal-flight-summary" style={{ marginBottom: '1.25rem' }}>
              {selectedFlight.legs && selectedFlight.legs.length === 2 ? (
                <>
                  <div className="summary-col"><span className="label">Outbound</span><span className="val">{selectedFlight.legs[0].flight_number} ({selectedFlight.legs[0].airline}) — {selectedFlight.legs[0].departure_airport} → {selectedFlight.legs[0].arrival_airport}</span></div>
                  <div className="summary-col"><span className="label">Return</span><span className="val">{selectedFlight.legs[1].flight_number} ({selectedFlight.legs[1].airline}) — {selectedFlight.legs[1].departure_airport} → {selectedFlight.legs[1].arrival_airport}</span></div>
                  <div className="summary-col"><span className="label">Dates</span><span className="val">{selectedFlight.legs[0].departure_time?.split('T')[0]} — {selectedFlight.legs[1].departure_time?.split('T')[0]}</span></div>
                </>
              ) : (
                <>
                  <div className="summary-col"><span className="label">Flight</span><span className="val">{selectedFlight.flight_number} ({selectedFlight.airline})</span></div>
                  <div className="summary-col">
                    <span className="label">Route</span>
                    <span className="val">
                      {selectedFlight.departure_airport} → {selectedFlight.arrival_airport}
                      {selectedFlight.segments && selectedFlight.segments.length > 1 && (
                        <span style={{ fontSize: '0.62rem', color: '#b45309', fontWeight: '700', display: 'block', marginTop: '2px' }}>
                          via {selectedFlight.segments.slice(0, -1).map(s => s.arrival_airport).join(', ')} ({selectedFlight.stops} stop{selectedFlight.stops > 1 ? 's' : ''})
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="summary-col"><span className="label">Date</span><span className="val">{selectedFlight.departure_time?.split('T')[0]}</span></div>
                </>
              )}
              <div className="summary-col"><span className="label">Cabin</span><span className="val">{selectedFlight.cabin_class}</span></div>
              <div className="summary-col"><span className="label">Brand</span><span className="val">{selectedFlight.fare_family}</span></div>
              <div className="summary-col">
                <span className="label">Baggage</span>
                <span className="val" style={{ fontSize: '0.75rem', fontWeight: '600' }}>
                  {selectedFlight.baggage_allowance && selectedFlight.baggage_allowance.length > 0
                    ? selectedFlight.baggage_allowance.map(b => `${b.type}: ${b.allowance}`).join(', ')
                    : '—'}
                </span>
              </div>
              <div className="summary-col"><span className="label">Price</span><span className="val highlight">{selectedFlight.currency} {(() => { const bdk = selectedFlight.price_breakdown ? Object.keys(selectedFlight.price_breakdown) : []; return (bdk.length > 0 ? bdk.reduce((s, k) => s + (selectedFlight.price_breakdown[k].total_price || 0), 0) : selectedFlight.price || 0).toLocaleString(undefined, {minimumFractionDigits: 2}); })()}</span></div>
            </div>

            {bookingError && <div className="error-banner">{bookingError}</div>}

            {/* STEP: Passenger Details Form */}
            {bookingStep === 'passenger' && (
              <form onSubmit={handlePassengerNext} className="booking-modal-form">
                {travelers.length > 1 && (
                  <div className="passenger-tabs" style={{ display: 'flex', gap: '0.35rem', marginBottom: '1.25rem', borderBottom: '1px solid var(--border-color)', paddingBottom: '0.65rem', overflowX: 'auto', scrollbarWidth: 'none' }}>
                    {travelers.map((t, idx) => {
                      const complete = t.first_name && t.last_name && t.date_of_birth && t.passport_number && t.passport_expiry && t.email && t.phone;
                      return (
                        <button
                          key={idx}
                          type="button"
                          className={`nav-tab ${activePassengerIdx === idx ? 'active' : ''}`}
                          onClick={() => setActivePassengerIdx(idx)}
                          style={{
                            padding: '0.45rem 1rem',
                            fontSize: '0.8rem',
                            borderRadius: '4px',
                            border: '1px solid ' + (activePassengerIdx === idx ? 'var(--gs-crimson)' : 'var(--border-color)'),
                            background: activePassengerIdx === idx ? 'var(--gs-crimson)' : '#f8fafc',
                            color: activePassengerIdx === idx ? 'white' : 'var(--text-secondary)',
                            fontWeight: '700',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '0.35rem',
                            whiteSpace: 'nowrap',
                            transition: 'all 0.15s'
                          }}
                        >
                          Passenger {idx + 1} ({t.passenger_type === 'ADT' ? 'Adult' : t.passenger_type === 'CNN' ? 'Child' : 'Infant'})
                          {complete ? (
                            <span style={{ color: activePassengerIdx === idx ? 'white' : '#16a34a', fontWeight: 'bold' }}>✓</span>
                          ) : (
                            <span style={{ color: activePassengerIdx === idx ? 'white' : 'var(--gs-crimson)', fontWeight: 'bold' }}>*</span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
                <div className="form-grid-2">
                  <div className="form-group">
                    <label className="form-label">First Name *</label>
                    <input type="text" className="form-input" required placeholder="John" value={travelers[activePassengerIdx]?.first_name || ''} onChange={e => handleTravelerChange('first_name', e.target.value.replace(/[^a-zA-Z\s]/g, ''))} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Last Name *</label>
                    <input type="text" className="form-input" required placeholder="Doe" value={travelers[activePassengerIdx]?.last_name || ''} onChange={e => handleTravelerChange('last_name', e.target.value.replace(/[^a-zA-Z\s]/g, ''))} />
                  </div>
                </div>
                <div className="form-grid-2">
                  <div className="form-group">
                    <label className="form-label">Date of Birth *</label>
                    <input type="date" className="form-input" required value={travelers[activePassengerIdx]?.date_of_birth || ''} onChange={e => handleTravelerChange('date_of_birth', e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Gender *</label>
                    <select className="form-select" value={travelers[activePassengerIdx]?.gender || 'Male'} onChange={e => handleTravelerChange('gender', e.target.value)}>
                      <option value="Male">Male</option>
                      <option value="Female">Female</option>
                    </select>
                  </div>
                </div>
                <div className="form-grid-2">
                  <div className="form-group">
                    <label className="form-label">Passport Number *</label>
                    <input type="text" className="form-input" required placeholder="N1234567" value={travelers[activePassengerIdx]?.passport_number || ''} onChange={e => handleTravelerChange('passport_number', e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Passport Expiry *</label>
                    <input type="date" className="form-input" required value={travelers[activePassengerIdx]?.passport_expiry || ''} onChange={e => handleTravelerChange('passport_expiry', e.target.value)} />
                  </div>
                </div>
                <div className="form-grid-2">
                  <div className="form-group">
                    <label className="form-label">Nationality</label>
                    <input type="text" className="form-input" placeholder="LK" maxLength={3} value={travelers[activePassengerIdx]?.nationality || ''} onChange={e => handleTravelerChange('nationality', e.target.value.toUpperCase())} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Phone *</label>
                    <input type="tel" className="form-input" required placeholder="+94771234567" value={travelers[activePassengerIdx]?.phone || ''} onChange={e => handleTravelerChange('phone', e.target.value)} />
                  </div>
                </div>
                <div className="form-group">
                  <label className="form-label">Email Address *</label>
                  <input type="email" className="form-input" required placeholder="john@example.com" value={travelers[activePassengerIdx]?.email || ''} onChange={e => handleTravelerChange('email', e.target.value)} />
                </div>
                <div className="modal-actions">
                  <button type="button" className="btn btn-secondary" onClick={closeBookingFlow}>Cancel</button>
                  <button type="submit" className="btn btn-primary">Continue to Seat Selection →</button>
                </div>
              </form>
            )}

            {/* STEP: Seats Loading */}
            {bookingStep === 'seats_loading' && (
              <div style={{ textAlign: 'center', padding: '2.5rem 1.5rem' }}>
                <div className="spinner" style={{ margin: '0 auto 1.25rem' }}></div>
                <h4 style={{ color: 'var(--gs-dark)', fontWeight: '700' }}>Fetching Live Seat Availability...</h4>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.5rem' }}>
                  Connecting to Travelport GDS to retrieve the real-time seat configuration and pricing for {selectedFlight.flight_number}.
                </p>
              </div>
            )}

            {/* seats_unavailable step removed — Travelport API response auto-routes to review */}

            {/* STEP: Seats Selector (Bird's Eye Airplane View) */}
            {bookingStep === 'seats' && seatMap && (
              <div style={{ display: 'flex', flexDirection: 'column', height: 'auto' }}>
                <div className="seat-selector-container" style={{ display: 'flex', gap: '1.5rem', marginTop: '0.5rem', maxHeight: '55vh', overflow: 'hidden' }}>
                  
                  {/* Left Sidebar */}
                  <div className="seat-selector-sidebar" style={{ width: '260px', display: 'flex', flexDirection: 'column', gap: '1.5rem', borderRight: '1px solid var(--border-color)', paddingRight: '1.25rem', overflowY: 'auto' }}>
                    <div>
                      <h4 style={{ fontSize: '0.9rem', fontWeight: '700', color: 'var(--gs-dark)', marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Passengers</h4>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                        {travelers.map((t, idx) => {
                          const assignedSeat = selectedSeats.find(s => s.passenger_idx === idx);
                          const isActive = activePassengerIdx === idx;
                          return (
                            <div
                              key={idx}
                              onClick={() => setActivePassengerIdx(idx)}
                              style={{
                                padding: '0.65rem 0.85rem',
                                borderRadius: '6px',
                                border: '1px solid ' + (isActive ? 'var(--gs-crimson)' : 'var(--border-color)'),
                                background: isActive ? 'var(--gs-crimson)' : '#f8fafc',
                                color: isActive ? 'white' : 'var(--text-secondary)',
                                cursor: 'pointer',
                                display: 'flex',
                                flexDirection: 'column',
                                gap: '0.25rem',
                                transition: 'all 0.15s'
                              }}
                            >
                              <div style={{ fontSize: '0.8rem', fontWeight: '700' }}>
                                Passenger {idx + 1}: {t.first_name} {t.last_name}
                              </div>
                              <div style={{ fontSize: '0.75rem', opacity: isActive ? 0.9 : 0.7, fontWeight: '500' }}>
                                Seat: <span style={{ fontWeight: '700', color: isActive ? 'white' : 'var(--gs-crimson)' }}>{assignedSeat ? `${assignedSeat.seat_number} (${assignedSeat.type})` : 'Not Selected'}</span>
                              </div>
                              {assignedSeat && assignedSeat.price > 0 && (
                                <div style={{ fontSize: '0.7rem', opacity: isActive ? 0.9 : 0.7, textAlign: 'right' }}>
                                  + {assignedSeat.currency} {assignedSeat.price.toLocaleString()}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    {/* Dynamic Seat Brand & Fare Inclusion Guide */}
                    {(() => {
                      const activeSeat = selectedSeats.find(s => s.passenger_idx === activePassengerIdx);
                      if (activeSeat) {
                        const isFree = activeSeat.price === 0;
                        return (
                          <div style={{
                            background: isFree ? '#f0fdf4' : '#fffbeb',
                            border: '1px solid ' + (isFree ? '#bbf7d0' : '#fde68a'),
                            borderRadius: '6px',
                            padding: '0.75rem',
                            fontSize: '0.75rem',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '0.35rem'
                          }}>
                            <div style={{ fontWeight: '800', color: isFree ? '#15803d' : '#b45309', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                              <span>{isFree ? '✓' : '⚠️'}</span>
                              <span>{isFree ? 'Fare Included' : 'Additional Charge'}</span>
                            </div>
                            <div style={{ color: isFree ? '#15803d' : '#b45309', fontWeight: '600', lineHeight: '1.3' }}>
                              {isFree
                                ? `Seat ${activeSeat.seat_number} (${activeSeat.type}) is fully included in your ${selectedFlight.fare_family} fare at no extra cost.`
                                : `Seat ${activeSeat.seat_number} (${activeSeat.type}) is NOT included in your ${selectedFlight.fare_family} fare. Selecting this seat adds an extra charge of ${activeSeat.currency} ${activeSeat.price.toLocaleString()}.`
                              }
                            </div>
                          </div>
                        );
                      }
                      return (
                        <div style={{
                          background: '#f8fafc',
                          border: '1px dashed #cbd5e1',
                          borderRadius: '6px',
                          padding: '0.75rem',
                          fontSize: '0.75rem',
                          color: '#64748b',
                          textAlign: 'center',
                          fontWeight: '600',
                          lineHeight: '1.3'
                        }}>
                          Select a seat on the cabin map for Passenger {activePassengerIdx + 1} to check if it's included in your {selectedFlight.fare_family} fare.
                        </div>
                      );
                    })()}

                    {/* Seat Legend */}
                    <div style={{ background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '6px', padding: '0.75rem', fontSize: '0.75rem' }}>
                      <h5 style={{ fontWeight: '700', marginBottom: '0.5rem', color: 'var(--gs-dark)' }}>Legend</h5>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                          <div style={{ width: '14px', height: '14px', borderRadius: '3px', border: '1px solid #cbd5e1', background: 'white' }}></div>
                          <span>Standard</span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                          <div style={{ width: '14px', height: '14px', borderRadius: '3px', border: '1px solid var(--gs-crimson)', background: 'var(--gs-crimson)' }}></div>
                          <span>Selected</span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                          <div style={{ width: '14px', height: '14px', borderRadius: '3px', border: '1px solid #94a3b8', background: '#cbd5e1' }}></div>
                          <span>Occupied</span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                          <div style={{ width: '14px', height: '14px', borderRadius: '3px', border: '1px solid #4f46e5', background: '#e0e7ff' }}></div>
                          <span>Front Row</span>
                        </div>
                      </div>
                    </div>

                    {/* Pricing Summary */}
                    {(() => {
                      const bdkSeat = selectedFlight.price_breakdown ? Object.keys(selectedFlight.price_breakdown) : [];
                      const flightGrandTotal = bdkSeat.length > 0
                        ? bdkSeat.reduce((s, k) => s + (selectedFlight.price_breakdown[k].total_price || 0), 0)
                        : (selectedFlight.price || 0);
                      const seatCharges = selectedSeats.reduce((acc, s) => acc + s.price, 0);
                      return (
                        <div style={{ marginTop: 'auto', background: '#fff8f8', border: '1px solid var(--gs-crimson)', borderRadius: '6px', padding: '0.75rem', fontSize: '0.8rem' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                            <span>Flight Fare ({travelers.length} Pax):</span>
                            <strong>{selectedFlight.currency} {flightGrandTotal.toLocaleString(undefined, {minimumFractionDigits: 2})}</strong>
                          </div>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                            <span>Seat Charges:</span>
                            <strong>{selectedFlight.currency} {seatCharges.toLocaleString(undefined, {minimumFractionDigits: 2})}</strong>
                          </div>
                          <div style={{ borderTop: '1px dashed var(--gs-crimson)', paddingTop: '0.4rem', marginTop: '0.4rem', display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem', color: 'var(--gs-crimson)' }}>
                            <span>Total Cost:</span>
                            <strong>{selectedFlight.currency} {(flightGrandTotal + seatCharges).toLocaleString(undefined, {minimumFractionDigits: 2})}</strong>
                          </div>
                        </div>
                      );
                    })()}
                  </div>

                  {/* Right Seat Map Chart - Top-down airplane styling */}
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', maxHeight: '100%' }}>
                    <div style={{ textAlign: 'center', padding: '0.25rem 0 0.5rem', borderBottom: '1px solid var(--border-color)', marginBottom: '0.75rem' }}>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Aircraft Cabin Map</div>
                      <strong style={{ fontSize: '0.85rem', color: 'var(--gs-dark)' }}>FRONT OF AIRCRAFT</strong>
                    </div>

                    {/* Scrollable airplane fuselage */}
                    <div style={{ flex: 1, overflowY: 'auto', padding: '0.5rem 1rem' }}>
                      
                      {/* Cockpit Nose */}
                      <div style={{
                        width: '290px',
                        height: '75px',
                        border: '2px solid #cbd5e1',
                        borderBottom: 'none',
                        borderTopLeftRadius: '150px',
                        borderTopRightRadius: '150px',
                        background: '#f1f5f9',
                        position: 'relative',
                        margin: '0 auto',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        boxShadow: '0 -4px 10px rgba(0,0,0,0.02)'
                      }}>
                        {/* Windshields */}
                        <div style={{ display: 'flex', gap: '8px', position: 'absolute', bottom: '15px' }}>
                          <div style={{ width: '35px', height: '14px', background: '#1e293b', borderTopLeftRadius: '10px', borderBottomLeftRadius: '2px', transform: 'skewY(-15deg)', border: '1px solid #64748b' }}></div>
                          <div style={{ width: '35px', height: '14px', background: '#1e293b', borderTopRightRadius: '10px', borderBottomRightRadius: '2px', transform: 'skewY(15deg)', border: '1px solid #64748b' }}></div>
                        </div>
                      </div>

                      {/* Fuselage body */}
                      <div style={{
                        width: '290px',
                        borderLeft: '2px solid #cbd5e1',
                        borderRight: '2px solid #cbd5e1',
                        background: '#f8fafc',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        margin: '0 auto',
                        padding: '1rem 0'
                      }}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', width: 'fit-content' }}>
                          
                          {/* Column Headers */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', marginBottom: '0.5rem', fontWeight: '700', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                            {(() => {
                              const cols = seatMap.columns || [];
                              const half = Math.ceil(cols.length / 2);
                              const leftCols = cols.slice(0, half);
                              const rightCols = cols.slice(half);
                              return (
                                <>
                                  <div style={{ display: 'flex', gap: '0.25rem' }}>
                                    {leftCols.map(c => (
                                      <div key={c.value} style={{ width: '32px', textAlign: 'center' }}>{c.value}</div>
                                    ))}
                                  </div>
                                  <div style={{ width: '32px', textAlign: 'center' }}></div>
                                  <div style={{ display: 'flex', gap: '0.25rem' }}>
                                    {rightCols.map(c => (
                                      <div key={c.value} style={{ width: '32px', textAlign: 'center' }}>{c.value}</div>
                                    ))}
                                  </div>
                                </>
                              );
                            })()}
                          </div>

                          {/* Row entries */}
                          {(() => {
                            const getRowType = (row) => {
                              const nonCols = (row.seats || []).filter(s => s && s.type && s.type !== 'empty');
                              if (nonCols.length === 0) return null;
                              const typeCounts = {};
                              nonCols.forEach(s => {
                                typeCounts[s.type] = (typeCounts[s.type] || 0) + 1;
                              });
                              let bestType = null;
                              let maxCount = -1;
                              for (const t in typeCounts) {
                                if (typeCounts[t] > maxCount) {
                                  maxCount = typeCounts[t];
                                  bestType = t;
                                }
                              }
                              const priceObj = nonCols.find(s => s.type === bestType) || nonCols[0];
                              return {
                                type: bestType,
                                price: priceObj.price || 0,
                                currency: priceObj.currency || 'LKR'
                              };
                            };

                            const rowMetadata = (seatMap.rows || []).map(r => getRowType(r));

                            const shouldShowHeader = (rowIdx) => {
                              const current = rowMetadata[rowIdx];
                              if (!current || !current.type) return false;
                              if (rowIdx === 0) return true;
                              const previous = rowMetadata[rowIdx - 1];
                              if (!previous || !previous.type) {
                                let foundPrev = false;
                                for (let k = rowIdx - 1; k >= 0; k--) {
                                  if (rowMetadata[k] && rowMetadata[k].type) {
                                    if (rowMetadata[k].type !== current.type) {
                                      return true;
                                    }
                                    foundPrev = true;
                                    break;
                                  }
                                }
                                return !foundPrev;
                              }
                              return current.type !== previous.type;
                            };

                            return seatMap.rows?.map((row, rowIdx) => {
                              const seats = row.seats || [];
                              const half = Math.ceil(seats.length / 2);
                              const leftSeats = seats.slice(0, half);
                              const rightSeats = seats.slice(half);
                              const isExitRow = row.row_number === 12 || row.row_number === 13;

                              return (
                                <div key={row.row_number || rowIdx} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%' }}>
                                  {shouldShowHeader(rowIdx) && (() => {
                                    const meta = rowMetadata[rowIdx];
                                    const isFree = meta.price === 0;
                                    return (
                                      <div style={{
                                        width: '100%',
                                        textAlign: 'center',
                                        margin: '0.85rem 0 0.5rem',
                                        position: 'relative',
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'center'
                                      }}>
                                        <div style={{ position: 'absolute', left: 0, right: 0, height: '1px', background: '#cbd5e1', zIndex: 1 }}></div>
                                        <span style={{
                                          position: 'relative',
                                          zIndex: 2,
                                          background: '#f8fafc',
                                          padding: '0.2rem 0.65rem',
                                          borderRadius: '20px',
                                          fontSize: '0.62rem',
                                          fontWeight: '800',
                                          color: isFree ? '#16a34a' : '#d97706',
                                          border: '1px solid ' + (isFree ? '#bbf7d0' : '#fef3c7'),
                                          background: isFree ? '#f0fdf4' : '#fffbeb',
                                          textTransform: 'uppercase',
                                          letterSpacing: '0.04em',
                                          whiteSpace: 'nowrap'
                                        }}>
                                          ✈ {meta.type} {meta.price > 0 ? `(+ ${meta.currency} ${meta.price.toLocaleString()})` : '(Included)'}
                                        </span>
                                      </div>
                                    );
                                  })()}

                                  {isExitRow && (
                                    <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', fontSize: '0.6rem', color: '#b45309', background: '#fef3c7', padding: '0.15rem 0.5rem', borderRadius: '4px', margin: '0.25rem 0', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.04em', boxSizing: 'border-box' }}>
                                      <span>⟨ Exit Row</span>
                                      <span>Exit Row ⟩</span>
                                    </div>
                                  )}

                                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', margin: '0.15rem 0' }}>
                                    <div style={{ display: 'flex', gap: '0.25rem' }}>
                                      {leftSeats.map((seat, idx) => renderSeat(seat, idx))}
                                    </div>

                                    <div style={{
                                      width: '32px',
                                      height: '32px',
                                      borderRadius: '50%',
                                      background: '#f1f5f9',
                                      border: '1px solid #e2e8f0',
                                      display: 'flex',
                                      alignItems: 'center',
                                      justifyContent: 'center',
                                      fontSize: '0.75rem',
                                      color: 'var(--text-secondary)',
                                      fontWeight: '700'
                                    }}>
                                      {row.row_number}
                                    </div>

                                    <div style={{ display: 'flex', gap: '0.25rem' }}>
                                      {rightSeats.map((seat, idx) => renderSeat(seat, idx))}
                                    </div>
                                  </div>
                                </div>
                              );
                            });
                          })()}

                        </div>
                      </div>

                      {/* Tail Cone */}
                      <div style={{
                        width: '290px',
                        height: '60px',
                        border: '2px solid #cbd5e1',
                        borderTop: 'none',
                        borderBottomLeftRadius: '80px 60px',
                        borderBottomRightRadius: '80px 60px',
                        background: '#f1f5f9',
                        margin: '0 auto',
                        position: 'relative',
                        boxShadow: '0 4px 10px rgba(0,0,0,0.02)'
                      }}>
                        {/* Tail fins */}
                        <div style={{ width: '45px', height: '12px', background: '#cbd5e1', position: 'absolute', left: '-35px', bottom: '15px', borderTopLeftRadius: '12px', transform: 'rotate(-20deg)', zIndex: -1 }}></div>
                        <div style={{ width: '45px', height: '12px', background: '#cbd5e1', position: 'absolute', right: '-35px', bottom: '15px', borderTopRightRadius: '12px', transform: 'rotate(20deg)', zIndex: -1 }}></div>
                      </div>

                    </div>

                    <div style={{ textAlign: 'center', padding: '0.5rem 0 0.25rem', marginTop: '0.5rem' }}>
                      <strong style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>BACK OF AIRCRAFT</strong>
                    </div>
                  </div>

                </div>

                <div className="modal-actions" style={{ borderTop: '1px solid var(--border-color)', paddingTop: '1rem', marginTop: '1rem' }}>
                  <button type="button" className="btn btn-secondary" onClick={() => setBookingStep('passenger')}>
                    ← Back to Passenger
                  </button>
                  <div style={{ display: 'flex', gap: '0.5rem', marginLeft: 'auto' }}>
                    <button type="button" className="btn btn-secondary" onClick={() => {
                      setSelectedSeats([]);
                      setBookingStep('review');
                    }}>
                      Skip Seat Selection
                    </button>
                    <button type="button" className="btn btn-primary" onClick={() => {
                      setBookingError('');
                      setBookingStep('review');
                    }}>
                      Continue to Review →
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* STEP: Review */}
            {bookingStep === 'review' && (
              <div>
                <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '0.5rem', padding: '1.25rem', marginBottom: '1rem', maxHeight: '280px', overflowY: 'auto' }}>
                  <h4 style={{ marginBottom: '1rem', color: 'var(--gs-dark)', fontSize: '0.95rem', borderBottom: '2px solid var(--gs-crimson)', paddingBottom: '0.4rem' }}>Passenger Information</h4>
                  {travelers.map((t, idx) => {
                    const assignedSeat = selectedSeats.find(s => s.passenger_idx === idx);
                    return (
                      <div key={idx} style={{ marginBottom: idx < travelers.length - 1 ? '1.25rem' : '0', borderBottom: idx < travelers.length - 1 ? '1px dashed #e2e8f0' : 'none', paddingBottom: idx < travelers.length - 1 ? '1rem' : '0' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                          <div style={{ fontWeight: '700', fontSize: '0.82rem', color: 'var(--gs-crimson)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Passenger {idx + 1}</div>
                          {assignedSeat && (
                            <span style={{ fontSize: '0.75rem', color: '#1e1b4b', background: '#e0e7ff', padding: '0.15rem 0.5rem', borderRadius: '4px', fontWeight: '700' }}>
                              Seat: {assignedSeat.seat_number} ({assignedSeat.type})
                            </span>
                          )}
                        </div>
                        <div className="form-grid-2" style={{ fontSize: '0.85rem', gap: '0.5rem 1rem' }}>
                          <div><span style={{ color: 'var(--text-muted)' }}>Name: </span><strong>{t.first_name} {t.last_name}</strong></div>
                          <div><span style={{ color: 'var(--text-muted)' }}>DOB: </span><strong>{t.date_of_birth}</strong></div>
                          <div><span style={{ color: 'var(--text-muted)' }}>Passport: </span><strong>{t.passport_number}</strong></div>
                          <div><span style={{ color: 'var(--text-muted)' }}>Nationality: </span><strong>{t.nationality || 'LK'}</strong></div>
                          <div><span style={{ color: 'var(--text-muted)' }}>Email: </span><strong>{t.email}</strong></div>
                          <div><span style={{ color: 'var(--text-muted)' }}>Phone: </span><strong>{t.phone}</strong></div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* Flight Itinerary Details */}
                <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '0.5rem', padding: '1.25rem', marginBottom: '1rem' }}>
                  <h4 style={{ marginBottom: '1rem', color: 'var(--gs-dark)', fontSize: '0.95rem', borderBottom: '2px solid var(--gs-crimson)', paddingBottom: '0.4rem' }}>Flight Itinerary</h4>
                  {selectedFlight.legs && selectedFlight.legs.length === 2 ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                      {['Outbound', 'Return'].map((legLabel, lIdx) => {
                        const leg = selectedFlight.legs[lIdx];
                        return (
                          <div key={lIdx}>
                            <div style={{ fontSize: '0.68rem', fontWeight: '800', color: 'var(--gs-crimson)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '0.4rem' }}>{legLabel}</div>
                            {leg.segments && leg.segments.length > 0 ? (
                              <SegmentList segments={leg.segments} />
                            ) : (
                              <div style={{ fontSize: '0.8rem', display: 'flex', justifyContent: 'space-between' }}>
                                <strong>{leg.departure_airport} → {leg.arrival_airport}</strong>
                                <span>{leg.airline} {leg.flight_number}</span>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ) : selectedFlight.segments && selectedFlight.segments.length > 0 ? (
                    <SegmentList segments={selectedFlight.segments} />
                  ) : (
                    <div style={{ fontSize: '0.8rem', display: 'flex', justifyContent: 'space-between' }}>
                      <strong>{selectedFlight.departure_airport} → {selectedFlight.arrival_airport}</strong>
                      <span>{selectedFlight.airline} {selectedFlight.flight_number}</span>
                    </div>
                  )}
                </div>
                
{(() => {
                  const bdkReview = selectedFlight.price_breakdown ? Object.keys(selectedFlight.price_breakdown) : [];
                  const flightGrandTotalReview = bdkReview.length > 0
                    ? bdkReview.reduce((s, k) => s + (selectedFlight.price_breakdown[k].total_price || 0), 0)
                    : (selectedFlight.price || 0);
                  const seatChargesReview = selectedSeats.reduce((acc, s) => acc + s.price, 0);
                  return (
                    <div style={{ background: '#fff8f8', border: '1px solid var(--gs-crimson)', borderRadius: '0.5rem', padding: '1.25rem', marginBottom: '1rem' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                          <span>Flight Fare ({travelers.length} Pax):</span>
                          <strong>{selectedFlight.currency} {flightGrandTotalReview.toLocaleString(undefined, {minimumFractionDigits: 2})}</strong>
                        </div>
                        {selectedSeats.length > 0 && (
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                            <span>Seat Selection Charges:</span>
                            <strong>{selectedFlight.currency} {seatChargesReview.toLocaleString(undefined, {minimumFractionDigits: 2})}</strong>
                          </div>
                        )}
                        <div style={{ borderTop: '1px solid #cbd5e1', paddingTop: '0.5rem', marginTop: '0.25rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span style={{ color: 'var(--gs-dark)', fontSize: '0.9rem', fontWeight: '700' }}>Grand Total</span>
                          <strong style={{ color: 'var(--gs-crimson)', fontSize: '1.4rem' }}>
                            {selectedFlight.currency} {(flightGrandTotalReview + seatChargesReview).toLocaleString(undefined, {minimumFractionDigits: 2})}
                          </strong>
                        </div>
                      </div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>All prices are sourced in real-time from the Travelport GDS platform.</div>
                    </div>
                  );
                })()}

                <div className="modal-actions">
                  <button className="btn btn-secondary" onClick={() => setBookingStep(seatMap ? 'seats' : 'seats_unavailable')}>← Back</button>
                  <button className="btn btn-primary" onClick={() => setBookingStep('payment')}>Proceed to Payment →</button>
                </div>
              </div>
            )}

            {/* STEP: Payment Details Checkout */}
            {bookingStep === 'payment' && (
              <div>
                <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '0.5rem', padding: '1.25rem', marginBottom: '1rem' }}>
                  <h4 style={{ marginBottom: '1rem', color: 'var(--gs-dark)', fontSize: '0.95rem', borderBottom: '2px solid var(--gs-crimson)', paddingBottom: '0.4rem' }}>Select Payment Option</h4>
                  
                  {/* Payment method selector */}
                  <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.25rem' }}>
                    <button
                      type="button"
                      className={`btn ${paymentMethod === 'card' ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => setPaymentMethod('card')}
                      style={{ flex: 1, padding: '0.6rem', fontSize: '0.85rem', fontWeight: '700' }}
                    >
                      💳 Credit / Debit Card
                    </button>
                    <button
                      type="button"
                      className={`btn ${paymentMethod === 'bank' ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => setPaymentMethod('bank')}
                      style={{ flex: 1, padding: '0.6rem', fontSize: '0.85rem', fontWeight: '700' }}
                    >
                      🏦 Bank Transfer
                    </button>
                    <button
                      type="button"
                      className={`btn ${paymentMethod === 'cash' ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => setPaymentMethod('cash')}
                      style={{ flex: 1, padding: '0.6rem', fontSize: '0.85rem', fontWeight: '700' }}
                    >
                      💵 Cash Payment
                    </button>
                  </div>

                  {paymentMethod === 'card' ? (
                    <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: '1.6', background: 'white', padding: '1rem', borderRadius: '6px', border: '1px solid #cbd5e1' }}>
                      <p style={{ fontWeight: '700', color: 'var(--gs-crimson)', marginBottom: '0.5rem', fontSize: '0.85rem' }}>💳 Credit / Debit Card Payment</p>
                      <p style={{ fontSize: '0.8rem', color: '#475569', marginBottom: '0.5rem' }}>
                        Your reservation will be confirmed directly with <strong>Travelport GDS</strong> upon clicking <em>Confirm Booking</em>.
                      </p>
                      <p style={{ fontSize: '0.78rem', color: '#475569' }}>
                        A George Steuart Travel agent will contact you to process your card payment securely.
                      </p>
                      <div style={{ marginTop: '0.75rem', fontSize: '0.72rem', color: '#64748b', background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '6px', padding: '0.5rem 0.75rem' }}>
                        ℹ️ No card details are transmitted through this system. Payment is processed offline by George Steuart Travel.
                      </div>
                    </div>
                  ) : paymentMethod === 'bank' ? (
                    <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: '1.6', background: 'white', padding: '1rem', borderRadius: '6px', border: '1px solid #cbd5e1' }}>
                      <p style={{ fontWeight: '700', color: 'var(--gs-crimson)', marginBottom: '0.5rem', fontSize: '0.85rem' }}>George Steuart Travel Bank Details:</p>
                      <strong>Bank:</strong> Hatton National Bank (HNB)<br />
                      <strong>Account Name:</strong> George Steuart Travel Ltd<br />
                      <strong>Account Number:</strong> 003010012345<br />
                      <strong>Branch:</strong> Head Office<br />
                      <p style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: 'var(--text-muted)', borderTop: '1px solid #e2e8f0', paddingTop: '0.5rem' }}>
                        Please email bank transfer transaction slip to billing@georgesteuart.lk. Tickets will be issued upon transaction confirmation.
                      </p>
                    </div>
                  ) : (
                    <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: '1.6', background: 'white', padding: '1rem', borderRadius: '6px', border: '1px solid #cbd5e1' }}>
                      <p style={{ fontWeight: '700', color: 'var(--gs-crimson)', marginBottom: '0.5rem', fontSize: '0.85rem' }}>💵 Cash Settlement at Office Counter</p>
                      <p style={{ fontSize: '0.8rem', color: '#475569', marginBottom: '0.5rem' }}>
                        Your reservation will be held on the <strong>Travelport GDS</strong> platform with a confirmed PNR.
                      </p>
                      <strong>Office Address:</strong> George Steuart Travel Ltd, #7C, W. A. D. Ramanayake Mawatha, Colombo 02<br />
                      <strong>Office Hours:</strong> Monday – Friday: 8:30 AM – 5:00 PM | Saturday: 8:30 AM – 1:00 PM<br />
                      <div style={{ marginTop: '0.75rem', fontSize: '0.72rem', color: '#64748b', background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '6px', padding: '0.5rem 0.75rem' }}>
                        ℹ️ Please settle payment in cash or cashier's check at our ticketing counter before the GDS ticketing deadline to finalize e-ticket issuance.
                      </div>
                    </div>
                  )}
                </div>

{(() => {
                  const bdkPay = selectedFlight.price_breakdown ? Object.keys(selectedFlight.price_breakdown) : [];
                  const flightGrandTotalPay = bdkPay.length > 0
                    ? bdkPay.reduce((s, k) => s + (selectedFlight.price_breakdown[k].total_price || 0), 0)
                    : (selectedFlight.price || 0);
                  const seatChargesPay = selectedSeats.reduce((acc, s) => acc + s.price, 0);
                  return (
                    <div style={{ background: '#fff8f8', border: '1px solid var(--gs-crimson)', borderRadius: '0.5rem', padding: '1rem', marginBottom: '1.25rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontWeight: '700', color: 'var(--gs-crimson)' }}>
                        <span style={{ fontSize: '0.88rem' }}>Amount to Charge:</span>
                        <span style={{ fontSize: '1.3rem' }}>
                          {selectedFlight.currency} {(flightGrandTotalPay + seatChargesPay).toLocaleString(undefined, {minimumFractionDigits: 2})}
                        </span>
                      </div>
                    </div>
                  );
                })()}

                <div className="modal-actions">
                  <button className="btn btn-secondary" onClick={() => setBookingStep('review')}>← Back to Review</button>
                  <button className="btn btn-primary" onClick={handleConfirmBooking}>
                    Confirm Booking
                  </button>
                </div>
              </div>
            )}

            {/* STEP: Processing */}
            {bookingStep === 'processing' && (
              <div style={{ textAlign: 'center', padding: '2rem' }}>
                <div className="spinner" style={{ margin: '0 auto 1rem' }}></div>
                <h4>Confirming Booking via Travelport GDS...</h4>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.5rem' }}>Connecting to GDS workbench → Committing reservation PNR → Issuing ticket</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── TICKET POPUP (STEP 10) ──────────────────────────────────────── */}
      {bookingStep === 'ticket' && issuedTicket && (
        <div className="modal-overlay animate-fade-only" style={{ zIndex: 9999, overflowY: 'auto', display: 'block', padding: '2rem 1rem' }}>
          {/* Main Modal Card */}
          <div className="modal-content glass-panel animate-fade ticket-print-wrapper" style={{ 
            maxWidth: '850px', 
            margin: '0 auto', 
            padding: 0, 
            overflow: 'hidden', 
            background: 'white', 
            color: '#1e293b', 
            borderRadius: '8px', 
            boxShadow: '0 10px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.1)',
            border: '1px solid #e2e8f0'
          }}>
            {/* NO PRINT MODAL CONTROLS */}
            <div className="no-print" style={{
              background: '#f8fafc',
              padding: '1rem 1.5rem',
              borderBottom: '1px solid #e2e8f0',
              display: 'flex',
              flexWrap: 'wrap',
              justifyContent: 'space-between',
              alignItems: 'center',
              gap: '0.6rem'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span style={{ fontSize: '1.25rem' }}>✈️</span>
                <span style={{ fontWeight: '700', fontSize: '0.95rem', color: '#1e293b' }}>Official E-Ticket & Itinerary</span>
              </div>
              <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                <button className="btn btn-secondary btn-sm" onClick={() => handleRefreshTicket(issuedTicket.locator_code || issuedTicket.pnr)} disabled={refreshingTicket} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', padding: '0.4rem 0.8rem', fontSize: '0.8rem', background: '#7c3aed', color: 'white', border: '1px solid #7c3aed' }}>
                  <span>🔄</span> {refreshingTicket ? 'Syncing...' : 'Sync PNR'}
                </button>
                <button className="btn btn-primary btn-sm" onClick={() => window.print()} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', padding: '0.4rem 0.8rem', fontSize: '0.8rem' }}>
                  <span>🖨️</span> Print
                </button>
                <button className="btn btn-secondary btn-sm" onClick={handleDownloadPDF} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', padding: '0.4rem 0.8rem', fontSize: '0.8rem', background: '#0284c7', color: 'white', border: '1px solid #0284c7' }}>
                  <span>📥</span> PDF
                </button>
                <button className="btn btn-secondary btn-sm" onClick={handleShareWhatsApp} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', padding: '0.4rem 0.8rem', fontSize: '0.8rem', background: '#22c55e', color: 'white', border: '1px solid #22c55e' }}>
                  <span>💬</span> WhatsApp
                </button>
                <button className="btn btn-secondary btn-sm" onClick={handleShareEmail} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', padding: '0.4rem 0.8rem', fontSize: '0.8rem', background: '#64748b', color: 'white', border: '1px solid #64748b' }}>
                  <span>✉️</span> Email
                </button>
                <button className="btn btn-secondary btn-sm" onClick={closeBookingFlow} style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem' }}>
                  Close
                </button>
              </div>
            </div>

            {/* THE PRINTABLE TICKET RECEIPT */}
            <div id="electronic-ticket-receipt" className="ticket-receipt-body" style={{ padding: '2.5rem', background: 'white' }}>

              {/* Header: Logo, Agency Info, Ticket Title */}
              <div className="ticket-header-row" style={{ display: 'flex', flexWrap: 'wrap', gap: '1.25rem', justifyContent: 'space-between', alignItems: 'flex-start', borderBottom: '3px solid #c3122e', paddingBottom: '1.5rem', marginBottom: '2rem' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
                    <img src={gsLogo} alt="George Steuart Travel" style={{ height: '45px', objectFit: 'contain' }} />
                    <span style={{ fontSize: '1.2rem', fontWeight: '800', letterSpacing: '0.05em', color: '#1e293b', borderLeft: '2px solid #cbd5e1', paddingLeft: '0.75rem' }}>TRAVEL</span>
                  </div>
                  <div style={{ fontSize: '0.75rem', color: '#64748b', lineHeight: '1.4' }}>
                    <strong>George Steuart Travel Ltd.</strong> (Established 1835)<br />
                    14, Leyden Bastian Road, Colombo 01, Sri Lanka<br />
                    Tel: +94 11 232 5311 | Email: support@georgesteuart.lk
                  </div>
                </div>
                <div className="ticket-title-block" style={{ textAlign: 'right' }}>
                  <h1 style={{ fontFamily: 'var(--font-heading)', color: '#c3122e', fontSize: '1.35rem', fontWeight: '700', margin: '0 0 0.5rem 0' }}>ELECTRONIC TICKET RECEIPT</h1>
                  <div style={{ fontSize: '0.75rem', color: '#64748b', lineHeight: '1.4' }}>
                    <strong>Booking Status:</strong> <span style={{ color: issuedTicket.status === 'Ticketed' ? '#0f766e' : '#b45309', fontWeight: '700' }}>{issuedTicket.status === 'Ticketed' ? 'ISSUED / CONFIRMED' : 'RESERVATION ON HOLD'}</span><br />
                    <strong>Date of Issue:</strong> {issuedTicket.booking_date || new Date().toISOString().split('T')[0]}<br />
                    <strong>Issuing Agent:</strong> TripServices GDS (PCC: 7F3C)
                  </div>
                </div>
              </div>

              {/* Reference details banner */}
              <div className="ticket-ref-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1rem', background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '6px', padding: '1rem 1.25rem', marginBottom: '2rem' }}>
                <div>
                  <div style={{ fontSize: '0.68rem', color: '#64748b', textTransform: 'uppercase', fontWeight: '700', letterSpacing: '0.04em' }}>Agency Reference (PNR)</div>
                  <div style={{ fontSize: '1.2rem', fontWeight: '800', color: '#c3122e', fontFamily: 'monospace' }}>{issuedTicket.locator_code || issuedTicket.pnr}</div>
                </div>
                <div style={{ borderLeft: '1px dashed #cbd5e1', paddingLeft: '1rem' }}>
                  <div style={{ fontSize: '0.68rem', color: '#64748b', textTransform: 'uppercase', fontWeight: '700', letterSpacing: '0.04em' }}>Airline Confirmation</div>
                  <div style={{ fontSize: '1.2rem', fontWeight: '800', color: '#2563eb', fontFamily: 'monospace' }}>{issuedTicket.airline_pnr || 'Awaiting Code'}</div>
                  {issuedTicket.airline_pnr_source && <div style={{ fontSize: '0.65rem', color: '#64748b', marginTop: '0.1rem' }}>Carrier: {issuedTicket.airline_pnr_source}</div>}
                </div>
                <div style={{ borderLeft: '1px dashed #cbd5e1', paddingLeft: '1rem' }}>
                  <div style={{ fontSize: '0.68rem', color: '#64748b', textTransform: 'uppercase', fontWeight: '700', letterSpacing: '0.04em' }}>E-Ticket Number</div>
                  <div style={{ fontSize: '1.1rem', fontWeight: '800', color: '#0f766e', fontFamily: 'monospace' }}>{issuedTicket.ticket_number || 'PENDING'}</div>
                  <div style={{ fontSize: '0.65rem', color: '#64748b', marginTop: '0.1rem' }}>
                    {issuedTicket.ticket_number
                      ? '✓ Valid for Travel'
                      : issuedTicket.payment_method === 'Cash'
                        ? 'Awaiting Cash Settlement at Counter'
                        : issuedTicket.payment_method === 'Bank Transfer'
                          ? 'Awaiting Bank Slip Verification'
                          : 'GDS PNR Confirmed (Ticketing Pending Stock Allocation)'}
                  </div>
                </div>
              </div>

              {/* Grid: Left Section (Customer details + Itinerary) / Right Section (QR code + payment breakdown) */}
              <div style={{ display: 'flex', gap: '2rem', marginBottom: '2rem' }}>
                <div style={{ flex: 1.6 }}>
                  
                  {/* Passenger Information — all travelers */}
                  <h3 style={{ fontSize: '0.9rem', color: '#c3122e', borderBottom: '2px solid #e2e8f0', paddingBottom: '0.35rem', marginBottom: '0.75rem', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    Passenger Information ({travelers.length} {travelers.length === 1 ? 'Passenger' : 'Passengers'})
                  </h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem', marginBottom: '1.75rem' }}>
                    {travelers.map((t, pIdx) => {
                      const paxTypeLabel = t.passenger_type === 'CNN' ? 'Child' : t.passenger_type === 'INF' ? 'Infant' : 'Adult';
                      const paxIcon = t.passenger_type === 'CNN' ? '🧒' : t.passenger_type === 'INF' ? '👶' : '🧑';
                      // For pax 1, prefer the confirmed name from Travelport if available
                      const displayName = pIdx === 0 && issuedTicket.passenger_name
                        ? cleanPassengerName(issuedTicket.passenger_name)
                        : `${t.first_name} ${t.last_name}`.trim().toUpperCase();
                      return (
                        <div key={pIdx} style={{ border: '1px solid #e2e8f0', borderRadius: '6px', overflow: 'hidden', fontSize: '0.78rem' }}>
                          {/* Passenger card header */}
                          <div style={{ background: '#f1f5f9', padding: '0.4rem 0.75rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #e2e8f0' }}>
                            <span style={{ fontWeight: '700', color: '#1e293b', fontSize: '0.8rem' }}>
                              {paxIcon} Passenger {pIdx + 1}
                            </span>
                            <span style={{ fontSize: '0.68rem', fontWeight: '700', padding: '0.15rem 0.5rem', borderRadius: '20px', background: pIdx === 0 ? '#fee2e2' : '#eff6ff', color: pIdx === 0 ? '#c3122e' : '#1d4ed8' }}>
                              {paxTypeLabel} {pIdx === 0 ? '· Primary Contact' : ''}
                            </span>
                          </div>
                          {/* Passenger details table */}
                          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                            <tbody>
                              <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                                <td style={{ padding: '0.35rem 0.75rem', color: '#64748b', width: '38%', fontWeight: '500' }}>Full Name:</td>
                                <td style={{ padding: '0.35rem 0.75rem', fontWeight: '700', color: '#1e293b' }}>{displayName || '—'}</td>
                              </tr>
                              <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                                <td style={{ padding: '0.35rem 0.75rem', color: '#64748b', fontWeight: '500' }}>Passport Number:</td>
                                <td style={{ padding: '0.35rem 0.75rem', fontWeight: '600', color: '#1e293b' }}>{t.passport_number || (pIdx === 0 ? issuedTicket.passport_number : null) || '—'}</td>
                              </tr>
                              <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                                <td style={{ padding: '0.35rem 0.75rem', color: '#64748b', fontWeight: '500' }}>Passport Expiry:</td>
                                <td style={{ padding: '0.35rem 0.75rem', fontWeight: '600', color: '#1e293b' }}>{t.passport_expiry || (pIdx === 0 ? issuedTicket.passport_expiry : null) || '—'}</td>
                              </tr>
                              <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                                <td style={{ padding: '0.35rem 0.75rem', color: '#64748b', fontWeight: '500' }}>Nationality:</td>
                                <td style={{ padding: '0.35rem 0.75rem', fontWeight: '600', color: '#1e293b' }}>{t.nationality || (pIdx === 0 ? issuedTicket.nationality : null) || 'LK'}</td>
                              </tr>
                              <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                                <td style={{ padding: '0.35rem 0.75rem', color: '#64748b', fontWeight: '500' }}>Gender:</td>
                                <td style={{ padding: '0.35rem 0.75rem', fontWeight: '600', color: '#1e293b' }}>{t.gender || (pIdx === 0 ? issuedTicket.gender : null) || '—'}</td>
                              </tr>
                              <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                                <td style={{ padding: '0.35rem 0.75rem', color: '#64748b', fontWeight: '500' }}>Date of Birth:</td>
                                <td style={{ padding: '0.35rem 0.75rem', fontWeight: '600', color: '#1e293b' }}>{t.date_of_birth || (pIdx === 0 ? issuedTicket.date_of_birth : null) || '—'}</td>
                              </tr>
                              {pIdx === 0 && (
                                <>
                                  <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                                    <td style={{ padding: '0.35rem 0.75rem', color: '#64748b', fontWeight: '500' }}>Contact Email:</td>
                                    <td style={{ padding: '0.35rem 0.75rem', fontWeight: '600', color: '#1e293b' }}>{t.email || issuedTicket.email || '—'}</td>
                                  </tr>
                                  <tr>
                                    <td style={{ padding: '0.35rem 0.75rem', color: '#64748b', fontWeight: '500' }}>Contact Phone:</td>
                                    <td style={{ padding: '0.35rem 0.75rem', fontWeight: '600', color: '#1e293b' }}>{t.phone || issuedTicket.phone || '—'}</td>
                                  </tr>
                                </>
                              )}
                            </tbody>
                          </table>
                        </div>
                      );
                    })}
                  </div>

                  {/* Flight Itinerary */}
                  <h3 style={{ fontSize: '0.9rem', color: '#c3122e', borderBottom: '2px solid #e2e8f0', paddingBottom: '0.35rem', marginBottom: '0.75rem', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    Flight Itinerary & Segment Details
                  </h3>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                    <thead>
                      <tr style={{ borderBottom: '2px solid #cbd5e1', background: '#f8fafc', color: '#475569', fontWeight: '700' }}>
                        <th style={{ padding: '0.5rem', textAlign: 'left' }}>Flight / Carrier</th>
                        <th style={{ padding: '0.5rem', textAlign: 'left' }}>Departing</th>
                        <th style={{ padding: '0.5rem', textAlign: 'left' }}>Arriving</th>
                        <th style={{ padding: '0.5rem', textAlign: 'left' }}>Cabin / Seat</th>
                        <th style={{ padding: '0.5rem', textAlign: 'left' }}>Allowance</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(issuedTicket.legs && issuedTicket.legs.length > 1
                        ? issuedTicket.legs.flatMap(l => l.segments || [])
                        : (issuedTicket.segments || [])
                      ).length > 0 ? (
                        (issuedTicket.legs && issuedTicket.legs.length > 1
                          ? issuedTicket.legs.flatMap(l => l.segments || [])
                          : issuedTicket.segments
                        ).map((seg, sIdx) => (
                          <tr key={sIdx} style={{ borderBottom: '1px solid #cbd5e1' }}>
                            <td style={{ padding: '0.6rem 0.5rem', fontWeight: '600' }}>
                              <span style={{ color: '#c3122e', fontWeight: '800' }}>{seg.carrier_name || seg.carrier}</span><br />
                              <span style={{ fontSize: '0.7rem', color: '#64748b' }}>Flight: {seg.flight_number}</span>
                            </td>
                            <td style={{ padding: '0.6rem 0.5rem' }}>
                              <strong style={{ fontSize: '0.85rem' }}>{seg.departure_airport}</strong><br />
                              <span>{seg.departure_time}</span>
                            </td>
                            <td style={{ padding: '0.6rem 0.5rem' }}>
                              <strong style={{ fontSize: '0.85rem' }}>{seg.arrival_airport}</strong><br />
                              <span>{seg.arrival_time}</span>
                            </td>
                            <td style={{ padding: '0.6rem 0.5rem' }}>
                              <strong>{issuedTicket.cabin_class || 'Economy'}</strong><br />
                              <span style={{ fontSize: '0.75rem', color: '#2563eb', fontWeight: '700' }}>
                                {sIdx === 0 && issuedTicket.seat_number ? `Seat: ${issuedTicket.seat_number}` : '—'}
                              </span>
                            </td>
                            <td style={{ padding: '0.6rem 0.5rem', fontSize: '0.7rem', color: '#475569' }}>
                              <span>Bag: {issuedTicket.baggage_allowance || '—'}</span><br />
                              <span>Duration: {seg.duration?.replace('PT','').replace('H','h ').replace('M','m')}</span>
                              {seg.layover_minutes !== undefined && (
                                <div style={{ fontSize: '0.65rem', color: '#b45309', fontWeight: '700', marginTop: '2px' }}>
                                  Layover: {Math.floor(seg.layover_minutes / 60)}h {seg.layover_minutes % 60}m at {seg.arrival_airport}
                                </div>
                              )}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr style={{ borderBottom: '1px solid #cbd5e1' }}>
                          <td style={{ padding: '0.6rem 0.5rem', fontWeight: '600' }}>
                            <span style={{ color: '#c3122e', fontWeight: '800' }}>{issuedTicket.airline}</span><br />
                            <span style={{ fontSize: '0.7rem', color: '#64748b' }}>Flight: {issuedTicket.flight_number}</span>
                          </td>
                          <td style={{ padding: '0.6rem 0.5rem' }}>
                            <strong style={{ fontSize: '0.85rem' }}>{issuedTicket.departure_airport}</strong><br />
                            <span>{issuedTicket.departure_time}</span>
                          </td>
                          <td style={{ padding: '0.6rem 0.5rem' }}>
                            <strong style={{ fontSize: '0.85rem' }}>{issuedTicket.arrival_airport}</strong><br />
                            <span>{issuedTicket.arrival_time}</span>
                          </td>
                          <td style={{ padding: '0.6rem 0.5rem' }}>
                            <strong>{issuedTicket.cabin_class}</strong><br />
                            <span style={{ fontSize: '0.75rem', color: '#2563eb', fontWeight: '700' }}>Seat: {issuedTicket.seat_number || 'Not assigned'}</span>
                          </td>
                          <td style={{ padding: '0.6rem 0.5rem', fontSize: '0.7rem', color: '#475569' }}>
                            <span>Bag: {issuedTicket.baggage_allowance || '—'}</span><br />
                            <span>Fare Code: {issuedTicket.fare_basis || '—'}</span>
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                  
                </div>

                {/* Right Side Panel: Fast Track QR Code + Payment Box */}
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                  
                  {/* Fast Track Box */}
                  <div style={{ 
                    border: '2px solid #c3122e', 
                    borderRadius: '8px', 
                    padding: '1.25rem', 
                    textAlign: 'center', 
                    background: '#fffcfc'
                  }}>
                    <span style={{ 
                      display: 'inline-block', 
                      background: '#c3122e', 
                      color: 'white', 
                      padding: '0.2rem 0.6rem', 
                      borderRadius: '4px', 
                      fontSize: '0.65rem', 
                      fontWeight: '700', 
                      letterSpacing: '0.08em',
                      marginBottom: '0.75rem' 
                    }}>
                      FAST-TRACK ACCESS
                    </span>
                    <div style={{ display: 'flex', justifyContent: 'center', margin: '0.5rem 0' }}>
                      <QRCodeSVG 
                        value={JSON.stringify({
                          agency: "George Steuart Travel",
                          pnr: issuedTicket.locator_code || issuedTicket.pnr,
                          flight: issuedTicket.flight_number,
                          passenger: cleanPassengerName(issuedTicket.passenger_name),
                          passport: issuedTicket.passport_number,
                          fast_track: true,
                          verification: "GDS Live Verification 7F3C"
                        })}
                        size={130}
                        level="H"
                        includeMargin={true}
                      />
                    </div>
                    <div style={{ fontSize: '0.75rem', fontWeight: '700', color: '#1e293b', margin: '0.4rem 0 0.2rem' }}>
                      Scan QR at Airport Gates
                    </div>
                    <div style={{ fontSize: '0.65rem', color: '#64748b', lineHeight: '1.3' }}>
                      Entitles bearer to priority check-in, premium airport security lane, and accelerated immigration clearance.
                    </div>
                  </div>

                  {/* Payment Summary */}
                  <div style={{ 
                    border: '1px solid #e2e8f0', 
                    borderRadius: '8px', 
                    padding: '1.25rem', 
                    background: '#f8fafc' 
                  }}>
                    <h4 style={{ margin: '0 0 0.75rem 0', fontSize: '0.8rem', fontWeight: '700', color: '#1e293b', textTransform: 'uppercase', borderBottom: '1px solid #cbd5e1', paddingBottom: '0.35rem' }}>
                      Fare & Payment Details
                    </h4>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', fontSize: '0.75rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', color: '#64748b' }}>
                        <span>Flight Fare (from Travelport GDS):</span>
                        <span>{issuedTicket.currency} {((issuedTicket.total_fare || 0) - (issuedTicket.seat_charge || 0)).toLocaleString(undefined, {minimumFractionDigits: 2})}</span>
                      </div>
                      {issuedTicket.seat_charge > 0 && (
                        <div style={{ display: 'flex', justifyContent: 'space-between', color: '#64748b' }}>
                          <span>Seat Selection Fee:</span>
                          <span>{issuedTicket.currency} {issuedTicket.seat_charge.toLocaleString(undefined, {minimumFractionDigits: 2})}</span>
                        </div>
                      )}
                      <div style={{ borderTop: '1px dashed #cbd5e1', margin: '0.25rem 0' }}></div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: '700', color: '#c3122e', fontSize: '0.9rem' }}>
                        <span>Grand Total Paid:</span>
                        <span>{issuedTicket.currency} {issuedTicket.total_fare?.toLocaleString(undefined, {minimumFractionDigits: 2})}</span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', color: '#64748b', fontSize: '0.65rem', marginTop: '0.5rem' }}>
                        <span>Booking Status:</span>
                        <strong style={{ color: '#0f766e' }}>{issuedTicket.status || 'CONFIRMED'}</strong>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', color: '#64748b', fontSize: '0.65rem' }}>
                        <span>Payment Settlement:</span>
                        <span>To be collected by George Steuart Travel</span>
                      </div>
                    </div>
                  </div>

                </div>
              </div>

              {/* Important Notices */}
              <div style={{ borderTop: '2px solid #cbd5e1', paddingTop: '1.25rem', fontSize: '0.68rem', color: '#64748b', lineHeight: '1.4' }}>
                <h4 style={{ color: '#1e293b', fontWeight: '700', margin: '0 0 0.4rem 0', textTransform: 'uppercase' }}>
                  Important Travel Information
                </h4>
                <ul style={{ paddingLeft: '1.25rem', margin: 0 }}>
                  <li>Please carry a printout of this e-ticket receipt or have it ready on your mobile device during check-in.</li>
                  <li>Most international flights require passengers to arrive at the airport check-in counter at least 3 hours before departure. Check-in gates close exactly 60 minutes prior.</li>
                  <li><strong>Passport Validity:</strong> Ensure your passport is valid for at least 6 months from the date of your journey. Many destinations require specific entry visas.</li>
                  <li>Baggage policies may vary. Please contact George Steuart Travel Support or the airline directly to purchase extra allowance.</li>
                  <li>For support, flight updates, changes or cancellations, please contact the George Steuart Travel desk at +94 11 232 5311 or email travel@georgesteuart.lk.</li>
                </ul>
              </div>

              {/* Live verification footer */}
              <div style={{ textAlign: 'center', color: '#94a3b8', fontSize: '0.6rem', borderTop: '1px dashed #e2e8f0', marginTop: '1.5rem', paddingTop: '0.75rem' }}>
                Secure Live Flight Booking powered by Travelport TripServices v11 API. © {new Date().getFullYear()} George Steuart Travel. All rights reserved.
              </div>

            </div>

            <div className="no-print" style={{ 
              background: '#f8fafc', 
              padding: '1.25rem 1.5rem', 
              borderTop: '1px solid #e2e8f0', 
              display: 'flex', 
              justifyContent: 'center', 
              gap: '0.75rem',
              flexWrap: 'wrap'
            }}>
              <button className="btn btn-secondary" onClick={() => handleRefreshTicket(issuedTicket.locator_code || issuedTicket.pnr)} disabled={refreshingTicket} style={{ background: '#7c3aed', color: 'white', border: '1px solid #7c3aed' }}>🔄 {refreshingTicket ? 'Syncing...' : 'Sync PNR'}</button>
              <button className="btn btn-secondary" onClick={() => window.print()}>🖨 Print Ticket</button>
              <button className="btn btn-secondary" onClick={handleDownloadPDF} style={{ background: '#0284c7', color: 'white', border: '1px solid #0284c7' }}>📥 Download PDF</button>
              <button className="btn btn-secondary" onClick={handleShareWhatsApp} style={{ background: '#22c55e', color: 'white', border: '1px solid #22c55e' }}>💬 Share WhatsApp</button>
              <button className="btn btn-secondary" onClick={handleShareEmail} style={{ background: '#64748b', color: 'white', border: '1px solid #64748b' }}>✉️ Share Email</button>
              <button className="btn btn-primary" onClick={() => { closeBookingFlow(); setActiveTab('bookings'); }}>View All Bookings</button>
              <button className="btn btn-secondary" onClick={closeBookingFlow}>Close</button>
            </div>

          </div>
        </div>
      )}

      {/* Global Flight Search Loading Modal (Step 10 / Custom request) */}
      {loadingFlights && (
        <div className="modal-overlay animate-fade-only" style={{ zIndex: 99999, background: 'rgba(255, 255, 255, 0.95)' }}>
          <div style={{ textAlign: 'center', maxWidth: '550px', padding: '2.5rem 2rem' }}>
            <FlightSearchAnimation />
            <h3 style={{ color: 'var(--gs-crimson)', margin: '1.5rem 0 1rem', fontFamily: 'var(--font-heading)', fontSize: '1.35rem' }}>
              Searching Live Inventory
            </h3>
            <p style={{ color: '#475569', fontSize: '0.98rem', lineHeight: '1.6', fontWeight: '500' }}>
              Please wait while we search across multiple airlines to find the best available options for your journey
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function FlightSearchAnimation() {
  return (
    <div className="lottie-loader-container animate-fade">
      <lottie-player
        src="https://assets-v2.lottiefiles.com/a/cc98d310-116a-11ee-9baa-434a3bdd76b7/Gqbk5P02sM.json"
        background="transparent"
        speed="1"
        style={{ width: '100%', height: '100%', maxWidth: '350px', maxHeight: '300px' }}
        loop
        autoplay
      ></lottie-player>
    </div>
  );
}
