/* Ivory Digital Phase 5.5 — Google Places venue selection with manual fallback. */
(() => {
  const selected = new Map();
  let loader;

  function loadGoogleMaps(apiKey) {
    if (window.google?.maps?.importLibrary) return Promise.resolve(window.google.maps);
    if (loader) return loader;
    loader = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&loading=async&v=weekly`;
      script.async = true;
      script.onload = () => window.google?.maps?.importLibrary ? resolve(window.google.maps) : reject(new Error('Google Places did not load'));
      script.onerror = () => reject(new Error('Google Places did not load'));
      document.head.appendChild(script);
    });
    return loader;
  }

  function coordinates(location) {
    if (!location) return {latitude:null, longitude:null};
    return {
      latitude: typeof location.lat === 'function' ? location.lat() : location.lat,
      longitude: typeof location.lng === 'function' ? location.lng() : location.lng,
    };
  }

  async function enhance(form) {
    const config = form?.google_places || {};
    const venueQuestions = (form?.questions || []).filter(question => question.question_type === 'venue');
    if (!config.configured || !config.api_key || !venueQuestions.length) return;
    await loadGoogleMaps(config.api_key);
    const {PlaceAutocompleteElement} = await google.maps.importLibrary('places');
    for (const question of venueQuestions) {
      const input = document.querySelector(`#question-${question.id}`);
      if (!input || input.dataset.placesEnhanced === 'true') continue;
      input.dataset.placesEnhanced = 'true';
      input.placeholder = 'Or enter the venue manually';
      input.classList.add('venue-manual-entry');
      input.addEventListener('input', () => selected.delete(input.id));
      const widget = new PlaceAutocompleteElement();
      widget.className = 'venue-autocomplete-widget';
      widget.placeholder = 'Start typing the venue name or address';
      if (config.region_codes?.length) widget.includedRegionCodes = config.region_codes;
      input.before(widget);
      widget.addEventListener('gmp-select', async event => {
        const place = event.placePrediction.toPlace();
        await place.fetchFields({fields:['id','displayName','formattedAddress','location']});
        const point = coordinates(place.location);
        const details = {
          place_id: place.id || '',
          name: place.displayName || '',
          formatted_address: place.formattedAddress || '',
          latitude: point.latitude,
          longitude: point.longitude,
        };
        selected.set(input.id, details);
        input.value = details.name || details.formatted_address;
        input.dispatchEvent(new Event('change', {bubbles:true}));
        const note = input.parentElement.querySelector('.venue-ready');
        if (note) note.textContent = details.formatted_address ? `✓ ${details.formatted_address}` : '✓ Exact venue selected';
      });
    }
  }

  window.IvoryPlaces = {enhance, detailsFor: inputId => selected.get(inputId) || null};
})();
