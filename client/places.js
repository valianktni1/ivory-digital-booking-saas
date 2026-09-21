/* Ivory Digital Phase 5.5.2 — reliable Google Places loading with manual fallback. */
(() => {
  const selected = new Map();
  let loader;

  function loadGoogleMaps(apiKey) {
    if (window.google?.maps?.importLibrary) return window.google.maps.importLibrary('places');
    if (loader) return loader;

    /*
     * Google's supported dynamic-library bootstrap. Its internal callback
     * resolves only when importLibrary is genuinely ready. The previous
     * script.onload check could run too early when loading=async was used.
     */
    ((settings) => {
      let bootstrapPromise;
      let script;
      let key;
      const product = 'The Google Maps JavaScript API';
      const namespace = 'google';
      const importName = 'importLibrary';
      const callbackName = '__ib__';
      const doc = document;
      const root = window;
      const googleNamespace = root[namespace] || (root[namespace] = {});
      const mapsNamespace = googleNamespace.maps || (googleNamespace.maps = {});
      const requestedLibraries = new Set();
      const parameters = new URLSearchParams();
      const bootstrap = () => bootstrapPromise || (bootstrapPromise = new Promise(async (resolve, reject) => {
        script = doc.createElement('script');
        parameters.set('libraries', [...requestedLibraries].join(','));
        for (key in settings) {
          parameters.set(key.replace(/[A-Z]/g, letter => `_${letter[0].toLowerCase()}`), settings[key]);
        }
        parameters.set('callback', `${namespace}.maps.${callbackName}`);
        script.src = `https://maps.${namespace}apis.com/maps/api/js?${parameters}`;
        mapsNamespace[callbackName] = resolve;
        script.onerror = () => {
          bootstrapPromise = reject(new Error(`${product} could not load.`));
        };
        script.nonce = doc.querySelector('script[nonce]')?.nonce || '';
        doc.head.appendChild(script);
      }));

      if (mapsNamespace[importName]) {
        console.warn(`${product} only loads once. Ignoring:`, settings);
      } else {
        mapsNamespace[importName] = (library, ...args) => {
          requestedLibraries.add(library);
          return bootstrap().then(() => mapsNamespace[importName](library, ...args));
        };
      }
    })({key: apiKey, v: 'weekly'});

    loader = window.google.maps.importLibrary('places');
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
    const {PlaceAutocompleteElement} = await loadGoogleMaps(config.api_key);
    if (!PlaceAutocompleteElement) throw new Error('Google Places autocomplete is unavailable');
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
