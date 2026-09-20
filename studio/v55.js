/* Ivory Digital Phase 5.5 — tenant Calendar completion and venue directions. */
function calendarMoment(value){return value?new Date(value).toLocaleString('en-GB',{dateStyle:'medium',timeStyle:'short'}):'Not synced yet'}
function syncSummary(data){const summary=data.sync_summary||{};return `<div class="calendar-summary"><span><b>${summary.synced||0}</b> synced</span><span><b>${summary.pending||0}</b> waiting</span><span class="${summary.errors?'has-error':''}"><b>${summary.errors||0}</b> errors</span></div>`}

async function loadCalendarV55(){
  try{
    calendarData=await api('/api/studio/calendar');
    $('#calendar-title').textContent=calendarData.connected?`Connected as ${calendarData.google_account_email||'Google account'}`:'Not connected';
    $('#calendar-copy').innerHTML=calendarData.connected?`Events are kept in <strong>${escapeHtml(calendarData.calendar_name)}</strong>. Last successful sync: ${escapeHtml(calendarMoment(calendarData.last_synced_at))}.`:calendarData.platform_configured?'Connect this photographer’s own Google account. Couples are never invited or emailed by Google.':'Ivory Digital’s Google OAuth details must be added before studios can connect.';
    const actions=$('#calendar-actions');
    if(calendarData.connected){
      actions.innerHTML=`<div class="calendar-choice"><label for="calendar-picker">Calendar for this studio</label><div><select id="calendar-picker" disabled><option>Loading calendars…</option></select><button id="save-calendar" class="button primary" disabled>Use this calendar</button></div><small>Changing this deliberately moves Ivory Digital’s known events to the chosen calendar.</small></div>${syncSummary(calendarData)}${calendarData.last_error?`<p class="calendar-error">${escapeHtml(calendarData.last_error)}</p>`:''}<div class="calendar-buttons"><button id="reconnect-calendar" class="button quiet">Reconnect Google</button><button id="disconnect-calendar" class="text-action">Disconnect</button></div>`;
      $('#reconnect-calendar').onclick=connectGoogleCalendar;
      $('#disconnect-calendar').onclick=disconnectGoogleCalendar;
      try{
        const calendars=await api('/api/studio/calendar/calendars'),picker=$('#calendar-picker');
        picker.innerHTML=calendars.map(item=>`<option value="${escapeAttr(item.id)}" ${item.id===calendarData.calendar_id?'selected':''}>${escapeHtml(item.name)}${item.primary?' · Primary':''}</option>`).join('');
        picker.disabled=!calendars.length;$('#save-calendar').disabled=!calendars.length;
        $('#save-calendar').onclick=async()=>{const option=picker.selectedOptions[0];try{await api('/api/studio/calendar/settings',{method:'PUT',body:JSON.stringify({calendar_id:picker.value,calendar_name:option?.textContent||'Google Calendar'})});dashboard=await api('/api/studio/dashboard');render();await loadCalendarV55();toast('Calendar selected and every eligible date checked safely.')}catch(error){toast(error.message,true)}};
      }catch(error){actions.querySelector('.calendar-choice').innerHTML=`<strong>Calendars could not be loaded</strong><p>${escapeHtml(error.message)}</p><button id="retry-calendar-list" class="button quiet">Try again</button>`;$('#retry-calendar-list').onclick=loadCalendarV55}
    }else actions.innerHTML=`<button id="connect-calendar" class="button primary" ${calendarData.platform_configured?'':'disabled'}>Connect Google Calendar</button>`;
    $('#connect-calendar')?.addEventListener('click',connectGoogleCalendar);
    $('#date-block-list').innerHTML=calendarData.blocks.length?calendarData.blocks.map(block=>`<article class="panel date-block"><div><span>${formatDate(block.start_date)}${block.end_date!==block.start_date?' → '+formatDate(block.end_date):''}</span><h3>${escapeHtml(block.label)}</h3><p>${escapeHtml(block.notes||'No private note')}</p>${block.calendar?.html_link?`<a class="venue-directions" href="${escapeAttr(block.calendar.html_link)}" target="_blank" rel="noopener noreferrer">Open Google event ↗</a>`:''}</div><div><em class="calendar-state ${escapeAttr(block.calendar?.status||'pending')}">${escapeHtml(block.calendar?.status||'pending')}</em><button data-remove-block="${block.id}" class="text-action">Remove</button></div></article>`).join(''):empty('No dates blocked','Holidays and unavailable periods will appear here.');
    document.querySelectorAll('[data-remove-block]').forEach(button=>button.onclick=async()=>{await api(`/api/studio/date-blocks/${button.dataset.removeBlock}`,{method:'DELETE'});await loadCalendarV55();toast('Blocked period removed.')})
  }catch(error){toast(error.message,true)}
}
async function connectGoogleCalendar(){try{const result=await api('/api/studio/calendar/connect',{method:'POST'});location.href=result.authorization_url}catch(error){toast(error.message,true)}}
async function disconnectGoogleCalendar(){if(!confirm('Disconnect this Google account? Existing Google events remain in the calendar, while Ivory Digital keeps all booking records.'))return;await api('/api/studio/calendar/disconnect',{method:'POST'});await loadCalendarV55();toast('Google Calendar disconnected. Your booking records are unchanged.')}
loadCalendar=loadCalendarV55;
$('#sync-calendar').onclick=async()=>{const button=$('#sync-calendar');button.disabled=true;try{await api('/api/studio/calendar/sync',{method:'POST'});await loadCalendarV55();toast('Every eligible wedding and blocked date has been checked.')}catch(error){toast(error.message,true)}finally{button.disabled=false}};

function enhanceVenueDirections(){
  const header=$('.journey-hero p');
  if(!header||!activeJourney?.venue_maps_url||header.querySelector('.venue-directions'))return;
  const link=document.createElement('a');link.className='venue-directions';link.href=activeJourney.venue_maps_url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Get directions ↗';header.append(' · ',link);
}
const openJourneyBeforeV55=openJourneyV54;
openJourneyV54=async function(id,tab){await openJourneyBeforeV55(id,tab);enhanceVenueDirections()};
openJourney=async function(id){return openJourneyV54(id)};

calendarPanel=function(j){const state=j.calendar||{},label=(state.status||'not ready').replaceAll('_',' ');return `<article class="panel side-card"><span class="eyebrow">CALENDAR</span><h3>${escapeHtml(label)}</h3><p>${escapeHtml(state.last_error||'A secured booking is kept in this studio’s selected Google Calendar.')}</p><div class="side-card-actions">${state.html_link?`<a href="${escapeAttr(state.html_link)}" target="_blank" rel="noopener noreferrer">Open Google event ↗</a>`:''}<button id="retry-booking-calendar" class="text-action">Check calendar sync</button></div>${!j.special_payment_arrangement?'<button id="special-arrangement" class="text-action">Record an agreed pay-later arrangement</button>':'<span class="journey-chip">Special arrangement recorded</span>'}</article>`};
const wireJourneyTabBeforeV55=wireJourneyTab;
wireJourneyTab=function(){wireJourneyTabBeforeV55();$('#retry-booking-calendar')?.addEventListener('click',async()=>{try{await api(`/api/studio/bookings/${activeJourney.id}/calendar/sync`,{method:'POST'});await openJourneyV54(activeJourney.id,activeJourneyTab);toast('This wedding’s calendar status has been checked.')}catch(error){toast(error.message,true)}});enhanceVenueDirections()};

helpContextNames.calendar='Calendar & availability';
guidedTours.calendar=[['.calendar-connect','One private calendar per studio','Each photographer connects their own Google account, then deliberately chooses a writable calendar.'],['#calendar-picker','Choose the right calendar','Only calendars this photographer can add events to are offered. Changing it safely checks all known Ivory Digital events.'],['#sync-calendar','Check every connected date','Use this any time to retry pending or failed wedding and date-block events.'],['#date-block-form','Block time away','Block one day or a holiday period so public availability and Google Calendar stay together.']];
