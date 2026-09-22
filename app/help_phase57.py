"""Additive help articles; Manager-edited answers are never overwritten."""

PHASE57_HELP_ARTICLES = ({'slug': 'phase57-private-date-review',
  'title': 'Who sees date warnings?',
  'category': 'Enquiries',
  'contexts': ['enquiries', 'weddings', 'calendar'],
  'keywords': ['date clash', 'overlap', 'double booking', 'private warning'],
  'summary': 'Only your Studio sees date warnings.',
  'body': 'Open the enquiry or wedding to see its Private date review banner. It lists your own overlapping '
          'bookings and date blocks. Couples never see this warning or another booking’s details.\n'
          '\n'
          'Accepting a quote is not blocked. Review your coverage and contact the couple personally if '
          'needed.',
  'action_label': 'Open Enquiries',
  'action_route': 'enquiries',
  'tour_key': 'enquiries',
  'sort_order': 0},
 {'slug': 'phase57-booking-fee-deadline',
  'title': 'How do I change the booking fee deadline?',
  'category': 'Payments',
  'contexts': ['brand', 'payments'],
  'keywords': ['deposit deadline', 'booking fee due', 'payment days'],
  'summary': 'Choose how many days after acceptance the booking fee is due.',
  'body': 'Open Business & brand > Invoice and payment details. Set Booking fee due after acceptance (days), '
          'from 0 to 90, then save.\n'
          '\n'
          'This applies to newly accepted quotes. Existing invoices keep their deadlines.',
  'action_label': 'Open Business & brand',
  'action_route': 'brand',
  'tour_key': 'brand',
  'sort_order': 1},
 {'slug': 'phase57-prepared-scheduled-emails',
  'title': 'Where can I see emails due to send?',
  'category': 'Communications',
  'contexts': ['communications', 'workflow'],
  'keywords': ['scheduled emails', 'planned date', 'email preview', 'recipient'],
  'summary': 'Scheduled emails shows prepared messages, recipients and planned dates.',
  'body': 'Open Communications > Scheduled emails. Expand Preview message to read the prepared copy. Check '
          'its planned date, recipient and status. Review/edit or skip where offered.\n'
          '\n'
          'The global pause and approval rules still apply. Use Automation settings to change future rules. '
          'Editing a reusable template does not silently rewrite an already prepared message.',
  'action_label': 'Open Communications',
  'action_route': 'communications',
  'tour_key': 'communications',
  'sort_order': 2},
 {'slug': 'phase57-couple-saved-drafts',
  'title': 'Can couples save a form and finish later?',
  'category': 'Client portal',
  'contexts': ['documents', 'weddings'],
  'keywords': ['save draft', 'unfinished questionnaire', 'return later'],
  'summary': 'Couples can explicitly save a draft using their private link.',
  'body': 'In their private portal, the couple opens the form and chooses Save draft & return later. They '
          'can reopen the same private link on another device to continue. Saving a draft does not submit '
          'the form or trigger submission emails.\n'
          '\n'
          'They must select Send this form safely, or Update this form safely, when finished. Changes are '
          'not saved automatically. Final timings remain unavailable until their invitation email has '
          'actually been sent; previously submitted timings stay available.',
  'action_label': 'Open Contracts & forms',
  'action_route': 'documents',
  'tour_key': 'documents',
  'sort_order': 3},
 {'slug': 'phase57-studio-password-recovery',
  'title': 'What if I forget my Studio password?',
  'category': 'Account',
  'contexts': ['home', 'mailbox'],
  'keywords': ['forgot password', 'reset password', 'recovery link'],
  'summary': 'Request a one-use reset link from the sign-in page.',
  'body': 'Choose Forgot your password? on the Studio sign-in screen and enter your login email. Your '
          'verified studio mailbox sends the recovery link when available. If it cannot send, contact Ivory '
          'Digital for a private owner reset link.\n'
          '\n'
          'The link expires after 30 minutes and works once. Saving a new password signs out existing '
          'sessions. Never share a recovery link with a couple.',
  'action_label': 'Open Studio home',
  'action_route': 'home',
  'tour_key': '',
  'sort_order': 4},
 {'slug': 'phase57-studio-subscription-details',
  'title': 'Where can I see my subscription?',
  'category': 'Account',
  'contexts': ['home'],
  'keywords': ['my subscription', 'plan price', 'renewal', 'subscription payment'],
  'summary': 'My subscription shows your plan and recorded payments.',
  'body': 'Choose My subscription in the sidebar. Review your plan, trial or renewal date, and recorded '
          'subscription payments. Contact Ivory Digital to change your plan or discuss payment.\n'
          '\n'
          'This screen shows recorded payments; it does not automatically charge a card.',
  'action_label': 'Open Studio home',
  'action_route': 'home',
  'tour_key': 'home',
  'sort_order': 5},
 {'slug': 'phase57-couple-next-steps',
  'title': 'What does the couple see in their portal?',
  'category': 'Client portal',
  'contexts': ['weddings', 'packages'],
  'keywords': ['client portal', 'live total', 'couple next step', 'bank details'],
  'summary': 'The couple sees a next step, exact quote total and their own documents.',
  'body': 'The quote total updates when the couple chooses a package and extras, including required extras '
          'and adjustments. Amounts include pence. After acceptance, the portal guides them to their '
          'agreement, booking fee and forms.\n'
          '\n'
          'Payment details show the fee, balance deadline, bank instructions where configured, and invoice '
          'reference. Payments update when you record them. Your private Studio notes and workflow '
          'information are not included.\n'
          '\n'
          'If their confirmation email fails, the signed agreement remains downloadable. During account '
          'suspension, existing documents remain available read-only.',
  'action_label': 'Open Weddings',
  'action_route': 'weddings',
  'tour_key': 'weddings',
  'sort_order': 6})
