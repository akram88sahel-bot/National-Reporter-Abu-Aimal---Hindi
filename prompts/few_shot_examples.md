# Few-shot examples — English to Roman Urdu (news bullets)

Use these as reference examples. You can either paste them inside the system prompt
(best quality, slightly more tokens) or send them as `messages` with role="user"/"assistant"
before the real translation request.

The most important thing these examples teach the model:
same number of bullets, same markers, names/numbers untouched, natural Urdu phrasing.

------------------------------------------------------------
EXAMPLE 1 — headline + 4 bullets
------------------------------------------------------------
USER:
PM announces new tax relief package for salaried class
- Finance Minister confirms relief will start from July 2026
- Salary up to Rs 100,000 per month will be fully exempt
- The government expects to lose Rs 45 billion in revenue
- Opposition parties call the announcement "an election gimmick"

ASSISTANT:
Wazir-e-Azam ka salary-class ke liye naye tax relief package ka elaan
- Finance Minister ne tasdeeq ki hai ke relief July 2026 se shuru hoga
- 100,000 rupay mahana tak ki tankhwa mukammal tor par exempt hogi
- Hukumat ko 45 arab rupay ki revenue ka nuqsan hone ka imkaan
- Mukhalif jamaaton ne elaan ko "intikhabi hathyar" qarar de diya

------------------------------------------------------------
EXAMPLE 2 — sport, names + scores preserved
------------------------------------------------------------
USER:
🏏 Pakistan beat Australia by 5 wickets in the 3rd ODI
- Babar Azam scored 84 runs off 91 balls
- Shaheen Afridi took 3 wickets for 42 runs
- The series is now level at 2-2
- The final match will be played in Lahore on 12 March

ASSISTANT:
🏏 Pakistan ne teesre ODI mein Australia ko 5 wickets se shikast de di
- Babar Azam ne 91 balls par 84 runs banaye
- Shaheen Afridi ne 42 runs de kar 3 wickets haasil keen
- Series ab 2-2 se barabar ho gayi hai
- Aakhri match 12 March ko Lahore mein khela jayega

------------------------------------------------------------
EXAMPLE 3 — tech, English terms kept
------------------------------------------------------------
USER:
Google launches new AI model for smartphones
- The update is free for all Android users
- It can work offline without internet
- Battery usage will be reduced by up to 20%
- The feature will first reach Pixel and Samsung devices

ASSISTANT:
Google ne smartphones ke liye naya AI model launch kar diya
- Yeh update tamam Android users ke liye muft hai
- Yeh internet ke baghair offline bhi kaam kar sakta hai
- Battery ka istemaal 20% tak kam ho jayega
- Yeh feature sab se pehle Pixel aur Samsung devices tak pohnchay ga

------------------------------------------------------------
EXAMPLE 4 — world news, neutral tone
------------------------------------------------------------
USER:
Earthquake of magnitude 6.2 hits eastern Afghanistan
- At least 18 people were killed and 40 injured
- Rescue teams reached the affected area after six hours
- The UN has promised emergency aid
- Roads and communication networks remain badly damaged

ASSISTANT:
Mashriqi Afghanistan mein 6.2 intensity ka zalzala
- Kam az kam 18 afraad jaan-ba-haq aur 40 zakhmi ho gaye
- Rescue teams che ghantay baad mutasira ilaqay mein pohnch gayeen
- UN ne hungami emdad ka wada kiya hai
- Sarkein aur communication networks ko shadeed nuqsan pohncha

------------------------------------------------------------
EXAMPLE 5 — single headline only
------------------------------------------------------------
USER:
Petrol price may increase by Rs 8 per litre this week

ASSISTANT:
Is haftay petrol ki qeemat mein 8 rupay prati litre izafa hone ka imkaan

------------------------------------------------------------
WHAT A BAD TRANSLATION LOOKS LIKE (never do this)
------------------------------------------------------------
- "सरकार" or "حکومت"            -> wrong script (must be Roman only)
- "Sarkar ne tax kam kiya"        -> Hindi flavour, use "Hukumat ne tax mein kami ki"
- "Wazeer-e-Aala ne elaan kiya ke woh tax ko kam karein ge" -> literal, clumsy
- "Pak ne Aus ko haraya"          -> over-abbreviated, unnatural
- Dropping the 🏏 emoji            -> must be preserved
- Changing "6.2" to "chhe point do"-> digits must stay digits
- Adding "yaad rahe ke"            -> never add anything not in the source
