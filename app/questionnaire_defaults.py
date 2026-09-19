"""Complete, editable starter forms for new Ivory Digital studios.

The definitions are intentionally ordinary JSON data. Photographers can edit,
move or remove every question in Studio, while existing submissions retain the
exact template snapshot that the couple completed.
"""

from copy import deepcopy


def question(key: str, label: str, kind: str, section: str, section_title: str,
             required: bool = False, help_text: str = "", placeholder: str = "",
             options: list[str] | None = None) -> dict:
    return {
        "id": key,
        "label": label,
        "type": kind,
        "required": required,
        "help_text": help_text,
        "placeholder": placeholder,
        "options": options or [],
        "section": section,
        "section_title": section_title,
    }


BOOKING_QUESTIONS = [
    question("primary_full_name", "Your full name", "short_text", "about", "About you both", True),
    question("primary_phone", "Your telephone number", "phone", "about", "About you both", True),
    question("primary_email", "Your email address", "email", "about", "About you both", True),
    question("partner_full_name", "Your partner's full name", "short_text", "about", "About you both", True),
    question("partner_phone", "Your partner's telephone number", "phone", "about", "About you both"),
    question("partner_email", "Your partner's email address", "email", "about", "About you both"),
    question("street_address", "Home address", "long_text", "about", "About you both", True,
             "Please include the street and house name or number."),
    question("town", "Town", "short_text", "about", "About you both", True),
    question("county", "City or county", "short_text", "about", "About you both", True),
    question("postcode", "Postcode", "short_text", "about", "About you both", True),

    question("wedding_date", "Date of wedding or event", "date", "wedding", "Your wedding day", True),
    question("ceremony_time", "Ceremony or service time", "time", "wedding", "Your wedding day", True),
    question("ceremony_details", "Exact ceremony venue and full address", "long_text", "wedding", "Your wedding day", True),
    question("reception_details", "Exact reception venue and full address", "long_text", "wedding", "Your wedding day", True,
             "If it is the same as the ceremony venue, please say so."),

    question("package_selected", "Package selected", "short_text", "planning", "Package, payment and planning", True,
             "Use the package name shown on your accepted quote."),
    question("payment_plan", "Preferred payment plan", "single_choice", "planning", "Package, payment and planning", True,
             "Your photographer will confirm the dates on your invoice.", options=[
                 "Booking fee on booking, balance before the wedding",
                 "Booking fee followed by two equal payments",
                 "25% on booking, remaining 75% before the wedding",
             ]),
    question("wedding_party_size", "How many people will be in your wedding party?", "number", "planning", "Package, payment and planning", True),
    question("unique_events", "Are there any unique events happening that your photographer should know about?", "long_text", "planning", "Package, payment and planning", True,
             "For example, a special arrival, surprise or cultural tradition."),
    question("guest_uploads", "Would you like wedding-guest photo uploads if included with your package?", "yes_no", "planning", "Package, payment and planning"),
    question("highlight_music", "Music choices for your highlight video, if included", "long_text", "planning", "Package, payment and planning", False,
             "Please give two songs where possible."),
    question("additional_information", "Anything else you would like your photographer to know?", "long_text", "planning", "Package, payment and planning"),
]


FINAL_TIMINGS_QUESTIONS = [
    question("ceremony_time", "Final ceremony time", "time", "ceremony", "Ceremony and reception", True),
    question("ceremony_duration", "Expected ceremony length in minutes", "number", "ceremony", "Ceremony and reception", True),
    question("ceremony_venue", "Ceremony venue and full address", "long_text", "ceremony", "Ceremony and reception", True),
    question("reception_same", "Is the reception at the same venue?", "yes_no", "ceremony", "Ceremony and reception", True),
    question("reception_venue", "Reception venue and full address", "long_text", "ceremony", "Ceremony and reception", False,
             "Complete this if the reception is somewhere different."),

    question("prep_photos", "Would you like preparation photographs?", "yes_no", "preparations", "Preparations and travel", True),
    question("prep_person", "Who will be photographed getting ready?", "short_text", "preparations", "Preparations and travel"),
    question("prep_venue", "Preparation venue and full address", "long_text", "preparations", "Preparations and travel"),
    question("travel_minutes", "Travel time from preparations to the ceremony in minutes", "number", "preparations", "Preparations and travel"),
    question("start_choice", "Preferred photography start", "single_choice", "preparations", "Preparations and travel", False,
             options=["Use the photographer's recommended start", "Request an earlier start"]),
    question("requested_start", "Earlier start requested", "time", "preparations", "Preparations and travel", False,
             "Complete this only if you are requesting an earlier start."),
    question("prep_notes", "Preparation notes", "long_text", "preparations", "Preparations and travel", False,
             "Room details, access, parking or anything else that will help."),
    question("second_prep", "Second preparation location or person", "long_text", "preparations", "Preparations and travel"),

    question("group_photo_time", "Approximate time for group photographs", "time", "running_order", "Your running order"),
    question("meal_time", "Wedding breakfast or meal time", "time", "running_order", "Your running order"),
    question("speeches_time", "Speeches time", "time", "running_order", "Your running order"),
    question("speeches_position", "When are the speeches?", "single_choice", "running_order", "Your running order",
             options=["Before the meal", "Between courses", "After the meal"]),
    question("evening_time", "Time evening guests arrive", "time", "running_order", "Your running order"),
    question("cake_time", "Cake-cutting time", "time", "running_order", "Your running order"),
    question("first_dance_time", "First-dance time", "time", "running_order", "Your running order", True),
    question("later_event", "Is there an essential photograph or event after the first dance?", "yes_no", "running_order", "Your running order", True),
    question("later_event_name", "What is the later event?", "short_text", "running_order", "Your running order"),
    question("later_event_time", "Later event time", "time", "running_order", "Your running order"),
    question("extra_stops", "Extra venues, stops or travel during the day", "long_text", "running_order", "Your running order"),

    question("day_contact", "Wedding-day contact name", "short_text", "contacts", "Contacts and important details", True),
    question("day_mobile", "Wedding-day mobile number", "phone", "contacts", "Contacts and important details", True),
    question("coordinator", "Coordinator or venue contact", "short_text", "contacts", "Contacts and important details"),
    question("group_count", "Approximate number of formal group photographs", "single_choice", "contacts", "Contacts and important details", True,
             options=["None", "1-5", "6-10", "More than 10"]),
    question("important_notes", "Important family details, surprises or anything else your photographer should know", "long_text", "contacts", "Contacts and important details"),
    question("details_confirmed", "I have checked these timings and understand I can update them if plans change", "yes_no", "check", "Check and send", True),
]


DEFAULT_QUESTIONNAIRES = {
    "booking": {
        "form_type": "booking",
        "name": "Wedding Booking Form",
        "introduction": "Please complete these details so everything about your wedding, package and plans is safely kept together.",
        "questions": BOOKING_QUESTIONS,
        "is_active": True,
    },
    "final_timings": {
        "form_type": "final_timings",
        "name": "Final Wedding Timings",
        "introduction": "Please confirm the places, contacts and running order so your photographer can prepare properly for your wedding day.",
        "questions": FINAL_TIMINGS_QUESTIONS,
        "is_active": True,
    },
}


def default_questionnaire(form_type: str) -> dict:
    return deepcopy(DEFAULT_QUESTIONNAIRES[form_type])
