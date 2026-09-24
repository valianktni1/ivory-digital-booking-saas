"""Additive help; existing administrator edits stay intact."""
PHASE59_HELP_ARTICLES = (
    {'slug':'phase59-two-businesses','title':'How do I use my second business?', 'category':'Account',
     'contexts':['home','brand'], 'keywords':['second business','switch business','two businesses','seat'],
     'summary':'Ivory Digital can grant a second business free or set its subscription price.',
     'body':'Ask Ivory Digital to add the second business to your seat. Use Current business at the top of Studio to switch. Save unfinished edits first. Each business has its own branding, bank accounts, Stripe connection, packages, templates and couples. Only the owner automatically gains access to the new business. A paid business opens after payment is confirmed; free access is granted by Ivory Digital. Date warnings remain private to the photographer.',
     'action_label':'Open Home','action_route':'home','tour_key':'home','sort_order':0},
    {'slug':'phase59-subscription-stripe','title':'How do I pay my Ivory subscription?', 'category':'Account',
     'contexts':['home'], 'keywords':['subscription','card','Stripe','billing','second business price'],
     'summary':'Open Payments & subscription for the selected business.',
     'body':'The business owner can review the plan, price and billing cycle under Payments & subscription. If enabled by Ivory Digital, choose Subscribe to review and authorise the recurring payment in Stripe. A second business granted free shows Free access. Manage subscription opens Stripe billing for an existing subscription. You can also arrange bank transfer with Ivory Digital. Returning from checkout is not proof of payment: access opens when payment is confirmed.',
     'action_label':'Open Home','action_route':'home','tour_key':'home','sort_order':1},
    {'slug':'phase59-couple-card-payments','title':'Can couples pay by card or bank transfer?', 'category':'Payments',
     'contexts':['home','brand','weddings','payments'], 'keywords':['Stripe','card payment','deposit','booking fee','balance','refund'],
     'summary':'Card payments are optional. Bank transfer remains available.',
     'body':'The owner opens Payments & subscription, connects this business’s Stripe account and enables card payments after Stripe setup is complete. Couples see a card option for the booking fee or remaining balance alongside the bank details on their invoice. Use only one payment method for each payment. Verified card payments appear automatically: do not record the same card payment manually. Bank payments are still recorded by you. Process card refunds in Stripe; verified refunds appear in the invoice and produce a private review notice. Review disputes in Stripe. Test mode is labelled and takes no real money. Inactive businesses and couple previews cannot start card checkout.',
     'action_label':'Open Home','action_route':'home','tour_key':'home','sort_order':2},
)
