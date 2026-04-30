  Deepening Opportunities                                                                                                         
                                                                                                                                  
  1. Extract an EmailRouter module from gmail.py                                                                                  
                                                                                                                                  
  Files: app/gmail.py (_parse_pending_emails, lines ~280–400), app/watchers.py, app/models.py (find_fuzzy_application),           
  app/parsed_emails.py                                                                                                            
                                                                                                                                  
  Problem: The decision "where does this email go?" is orchestrated inside _parse_pending_emails() — a 110-line function that     
  directly calls watchers, fuzzy matching, status selection, and queue updates with no intermediate abstraction. Understanding the
   routing decision requires reading across 5 modules. It has zero testable seam: mocking requires injecting 7 dependencies       
  simultaneously.                                                                                                                 
                                                                                                                                  
  Solution: A new EmailRouter module with one deep interface — route_email(user_id, parsed_result, message_metadata) →            
  RoutingOutcome. The router owns: watcher lookup, fuzzy match fallback, forward-only status selection, and queue state writes.   
  gmail.py becomes a caller with no routing knowledge. The routing decision is testable without Gmail API mocks.                  
                                                                                                                                  
  Benefits: Locality — every routing bug lives in one module. Leverage — callers get a routing decision behind a 3-argument call; 
  today they get nothing (all logic is buried). Tests can cover watcher-match, fuzzy-match, and queue-fallback branches
  independently with plain dicts.                                                                                                 
                                                                                                                                  
  ---                                                                   
  2. Deepen routes.py into a thin HTTP adapter                                                                                    
                                                                                                                                  
  Files: app/routes.py (559 lines), app/models.py, app/watchers.py, app/parsed_emails.py                                          
                                                                                                                                  
  Problem: Every route function is a mini-transaction: it parses the HTTP request, makes multiple data-access calls, coordinates  
  between models and watchers, and builds the response — all inline. Deleting the business logic inside routes and putting it in  
  callers wouldn't concentrate complexity; it'd distribute it into an even worse form. The real issue is there's no intermediate  
  layer: routes reach directly into 6+ modules. delete_application_route() calls delete_watchers_for_application() then
  delete_application() with no rollback boundary. accept_email_route() rebuilds normalized payloads from scratch.

  Solution: An ApplicationService module (or deepen models.py itself) that wraps the coordinated operations: create(payload,      
  user_id), update(id, payload, user_id), delete(id, user_id). The service owns the watcher-coordination and transaction boundary.
   Routes call one function per operation and handle only HTTP parsing and response formatting.                                   
                                                                            
  Benefits: Leverage — the 3-call coordination (delete watchers + delete application, or update application + set watchers)       
  happens behind a single call surface. Locality — bugs in the "delete also cleans watchers" invariant live in one place. Tests
  can exercise business rules without the Flask test client.                                                                      
                                                                            
  ---                                                                   
  3. Formalize the ParseResult contract between email_parser and gmail.py
                                                                                                                                  
  Files: app/email_parser.py, app/gmail.py (_parse_pending_emails)
                                                                                                                                  
  Problem: email_parser.parse_job_email_strict() returns a plain dict. gmail.py trusts the shape — accessing is_job_related,      
  company, role, status, confidence, reasoning_summary — with no validation. If the parser changes a key name, gmail.py fails     
  silently (returns None on a missing key, quietly misroutes emails). There is no seam here: it's a hidden contract documented    
  only by reading both files.                                               
                                                                        
  Solution: A ParseResult TypedDict (or @dataclass) defined once and imported by both modules. The parser's return type is        
  explicit; gmail.py's consumption is type-checked. The interface shrinks — callers know exactly what they get.
                                                                                                                                  
  Benefits: Leverage — the parser's interface becomes self-documenting; no need to read the implementation to know the output     
  shape. Locality — key renames are caught at the seam, not at runtime. Tests can assert on the result type directly.
                                                                                                                                  
  ---                                                                       
  4. Centralize ParseStatus strings into one owner                      
                                                                                                                                  
  Files: app/parsed_emails.py, app/routes.py, app/gmail.py
                                                                                                                                  
  Problem: parsed_emails.py is the natural owner of email queue states (pending_review, paused, accepted, dismissed, auto_updated,
   not_job). But routes.py hardcodes the same strings — e.g. record.get("parse_status") not in {"pending_review", "paused"} at
  line ~281. There is no exported constant, no seam. Adding a new status requires updating at least 3 files and hoping you found  
  all the string literals.                                                  
                                                                        
  Solution: Export a ParseStatus enum or PARSE_STATUSES constants from parsed_emails.py. All other modules import and use the     
  constants. No module other than parsed_emails.py ever spells a status string.
                                                                                                                                  
  Benefits: Locality — the complete set of valid statuses lives in one file. Leverage — adding a new status is a one-line change. 
  The deletion test confirms parsed_emails.py earns its keep; this change makes it earn more.
                                                                                                                                  
  ---                                                                       
  5. Deepen validators.py (or dissolve it)                              
                                                                                                                                  
  Files: app/validators.py, app/models.py, app/routes.py
                                                                                                                                  
  Problem: validators.py fails the deletion test: removing it would not concentrate complexity because the complexity it claims to
   own is already duplicated in models.py (status/source enum checks at lines 91–94) and routes.py (inline payload rebuilding in
  accept_email_route()). form_payload() is a one-line wrapper around normalize_payload(). The module's interface is nearly as     
  complex as its implementation.                                            
                                                                        
  Solution (deepen): Make validators.py the single owner of ALL validation — required fields, status/source enum membership,      
  watcher pattern format, date format. Its interface becomes validate_application_payload(raw) → (clean_payload, errors). Models
  and routes both defer to it; neither re-checks enum membership. Alternatively (dissolve): inline the trivial logic into         
  models.py and delete the file.                                            
                                                                        
  Benefits (deepen path): Leverage — callers get a clean payload or an error list behind one call. Locality — validation bugs are 
  in one file, not three. Tests can assert on validation rules without a Flask context or a database.
                                                                                                                                  
  ---     