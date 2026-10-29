import { useState, useEffect } from 'react';

// Use whatever host the page was loaded from (not a hardcoded 'localhost') so
// this also works when someone on the same network opens this machine's LAN
// IP instead of localhost — the backend runs on the same machine, port 8000.
const API_BASE = import.meta.env.VITE_API_BASE || `http://${window.location.hostname}:8000/api`;

// Shared airport search/select widget backed by the ~7,900-airport reference
// table (/api/reference/airports) — used by the public flight search form
// and by admin screens (e.g. Terminal Connections) that need an airport
// picker instead of free-text entry, so values always match a real IATA code.
export default function AirportSearchSelect({ value, onChange, label, placeholder }) {
  const [searchTerm, setSearchTerm] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [displayName, setDisplayName] = useState('');

  // Fetch airport info on value change to update label
  useEffect(() => {
    if (value) {
      fetch(`${API_BASE}/reference/airports?q=${encodeURIComponent(value)}`)
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
    fetch(`${API_BASE}/reference/airports?q=${encodeURIComponent(query)}`)
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
    onChange(airport.iata, airport);
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
