"""Curated maternal-health knowledge base, indexed into FAISS at startup.

Content follows MoHFW / WHO guidance used in India's ANC programme (MCP card, PMSMA,
anaemia-mukt Bharat). Each document is short so retrieval stays precise. Add more by
appending to ``DOCUMENTS`` or loading JSON via ``KnowledgeBase.add``.
"""
from __future__ import annotations

from app.core.pregnancy import ANC_SCHEDULE
from app.knowledge.weeks import WEEKS

CATEGORIES = ["pregnancy_guide", "schemes", "nutrition", "mental_wellbeing", "exercise", "danger_signs",
              "anc_schedule", "symptoms", "reports"]


def _d(id_: str, category: str, title: str, text: str, title_hi: str = "", text_hi: str = "",
       weeks: tuple[int, int] | None = None, source: str = "MoHFW / WHO ANC guidance") -> dict:
    return {"id": id_, "category": category, "title": title, "title_hi": title_hi, "text": text,
            "text_hi": text_hi, "week_from": weeks[0] if weeks else None, "week_to": weeks[1] if weeks else None,
            "source": source}


DOCUMENTS: list[dict] = [
    # ---------------- Danger signs ----------------
    _d("ds-bleeding", "danger_signs", "Heavy bleeding",
       "Bleeding from the vagina at any time in pregnancy, especially bleeding that soaks a pad or comes with clots or pain, is an emergency. Lie down, call 108, and go to the nearest hospital now. Light spotting should still be checked at the PHC the same day.",
       "ज़्यादा खून बहना", "गर्भावस्था में खून आना, ख़ासकर पैड भीग जाए या थक्के/दर्द हो, तो यह आपातकाल है। लेट जाएँ, 108 पर कॉल करें और तुरंत अस्पताल जाएँ।"),
    _d("ds-movement", "danger_signs", "Reduced fetal movement",
       "After week 28, baby should move at least 10 times in 12 hours. If baby is moving much less than usual or has stopped moving, lie on your left side, drink something cold or sweet and count kicks for 2 hours. If still fewer than 10 movements, contact your ASHA and go to the facility today; if no movement at all, go immediately.",
       "बच्चे की हलचल कम", "28 हफ़्ते के बाद 12 घंटे में कम से कम 10 बार हलचल होनी चाहिए। कम हो तो बाईं करवट लेटें और गिनें। फिर भी कम हो तो आज ही अस्पताल जाएँ।"),
    _d("ds-headache", "danger_signs", "Severe headache, blurred vision, swelling of face/hands",
       "A headache that does not go away, especially with blurred vision, seeing spots, pain in the upper stomach, or swelling of the face and hands, can mean high blood pressure (pre-eclampsia). Get your BP checked today. If BP is 160/110 or above, or there are fits, go to the district hospital immediately.",
       "तेज़ सिरदर्द, धुंधला दिखना", "लगातार सिरदर्द, धुंधला दिखना या चेहरे-हाथों पर सूजन हाई BP का संकेत हो सकता है। आज ही BP जाँच कराएँ।"),
    _d("ds-convulsions", "danger_signs", "Convulsions (fits)",
       "Fits or shaking of the body in pregnancy (eclampsia) is life-threatening. Turn her on her left side, keep her safe from injury, do not put anything in her mouth, and call 108 immediately.",
       "दौरे पड़ना", "शरीर में झटके या दौरे पड़ना जानलेवा है। करवट पर लिटाएँ, मुँह में कुछ न डालें और तुरंत 108 बुलाएँ।"),
    _d("ds-breathing", "danger_signs", "Breathing difficulty or chest pain",
       "Struggling to breathe at rest, fast breathing, or chest pain needs emergency help. Sit upright and call 108. Severe anaemia can also cause breathlessness.",
       "साँस लेने में तकलीफ़", "आराम में भी साँस फूलना या सीने में दर्द हो तो सीधे बैठें और 108 पर कॉल करें।"),
    _d("ds-fever", "danger_signs", "High fever",
       "Fever with chills, burning urine, or foul-smelling vaginal discharge can mean an infection. Drink fluids and visit your PHC the same day. Do not take medicines without advice.",
       "तेज़ बुखार", "ठंड के साथ बुखार या बदबूदार स्राव संक्रमण हो सकता है। उसी दिन PHC जाएँ।"),
    _d("ds-water", "danger_signs", "Water breaking or leaking fluid",
       "A gush or steady leak of watery fluid before labour pains means the bag of water may have broken. Go to the facility now, even if there is no pain, and avoid putting anything inside the vagina.",
       "पानी की थैली फटना", "दर्द से पहले पानी आना शुरू हो जाए तो तुरंत अस्पताल जाएँ।"),
    _d("ds-abdominal", "danger_signs", "Severe abdominal pain",
       "Severe or constant pain in the belly, especially with bleeding or dizziness, needs urgent care. Call 108.",
       "पेट में तेज़ दर्द", "पेट में तेज़ या लगातार दर्द हो, ख़ासकर खून या चक्कर के साथ, तो 108 पर कॉल करें।"),
    _d("ds-numbers", "danger_signs", "Emergency numbers",
       "108: free ambulance for emergencies. 102: free pregnancy transport to and from the hospital (JSSK). 14416: Tele-MANAS, free 24×7 mental health support in your language. Keep your MCP card ready and tell a family member.",
       "आपातकालीन नंबर", "108: एम्बुलेंस। 102: गर्भवती महिलाओं के लिए मुफ़्त वाहन। 14416: Tele-MANAS मानसिक स्वास्थ्य सहायता।"),

    # ---------------- Nutrition ----------------
    _d("nu-iron", "nutrition", "Iron-rich foods for anaemia",
       "Eat iron-rich foods every day: green leafy vegetables (palak, methi, bathua, sarson), chana, rajma, masoor dal, til (sesame), ragi, bajra, gud (jaggery), dates, poha, and eggs, fish or meat if you eat them. Take vitamin C with them (lemon, amla, guava, orange, tomato) to absorb more iron. Avoid tea or coffee for one hour before and after meals and iron tablets.",
       "आयरन वाले खाद्य पदार्थ", "रोज़ पालक, मेथी, बथुआ, चना, राजमा, तिल, रागी, गुड़, खजूर खाएँ। साथ में नींबू या आँवला लें। खाने और आयरन की गोली के साथ चाय न पिएँ।"),
    _d("nu-ifa", "nutrition", "When and how to take iron (IFA) and calcium tablets",
       "From the fourth month (week 14), take one iron–folic acid (IFA) tablet daily for at least 180 days, and two calcium tablets daily. Take IFA after lunch or dinner with lemon water, not with tea, milk or calcium. Take calcium at a different time, for example at night. Black stools and mild constipation are normal with iron; eat fibre and drink water.",
       "आयरन और कैल्शियम की गोली", "चौथे महीने से रोज़ 1 आयरन की गोली (180 दिन) और 2 कैल्शियम की गोली लें। आयरन खाने के बाद नींबू पानी से लें, चाय या दूध से नहीं। कैल्शियम रात को लें।"),
    _d("nu-diet", "nutrition", "A balanced pregnancy diet",
       "Eat one extra meal a day from the second trimester. Each day include: cereals (roti, rice, millets), dal or beans, green vegetables, a seasonal fruit, milk or curd, and an egg, paneer or soy. Use iodised salt. Eat small, frequent meals if you feel nauseous. Take Take-Home Ration (THR) from the Anganwadi.",
       "संतुलित आहार", "दूसरी तिमाही से रोज़ एक बार ज़्यादा खाएँ। अनाज, दाल, हरी सब्ज़ी, फल, दूध/दही और अंडा या पनीर शामिल करें। आयोडीन नमक लें।"),
    _d("nu-hydration", "nutrition", "Water and hydration",
       "Drink 8–10 glasses (about 2–3 litres) of safe, boiled or filtered water a day, more in hot weather. Coconut water, lassi, nimbu pani and soups count. Dark urine, headache and dizziness can be signs you need more water.",
       "पानी", "रोज़ 8–10 गिलास साफ़ पानी पिएँ, गर्मी में ज़्यादा। नारियल पानी, लस्सी, नींबू पानी भी गिना जाता है।"),
    _d("nu-diabetes", "nutrition", "Eating with high blood sugar (gestational diabetes)",
       "If your sugar is high: eat small meals every 2–3 hours, choose whole grains (millets, brown rice, whole-wheat roti), add dal and vegetables to every meal, avoid sweets, sugary drinks, maida and fried snacks, and walk for 15 minutes after meals. Follow your doctor's advice on medicines and sugar checks.",
       "शुगर में खान-पान", "शुगर ज़्यादा हो तो थोड़ा-थोड़ा बार-बार खाएँ, मिठाई और मैदा न लें, खाने के बाद 15 मिनट टहलें।"),
    _d("nu-bp", "nutrition", "Eating with high blood pressure",
       "Reduce salt: avoid pickles, papad, namkeen and packaged snacks. Eat fresh fruit, vegetables, dal and curd. Keep taking calcium tablets. Never stop BP medicine on your own.",
       "हाई BP में खान-पान", "नमक कम करें, अचार-पापड़-नमकीन न खाएँ। BP की दवा अपने आप बंद न करें।"),
    _d("nu-safety", "nutrition", "Food safety and myths",
       "Eat freshly cooked food and wash fruits and vegetables. Ripe papaya in normal amounts is safe; avoid large amounts of raw papaya. Avoid alcohol, tobacco, gutkha and paan masala completely. Eating more ghee does not make delivery easier; a balanced diet does.",
       "खाने की सुरक्षा और भ्रांतियाँ", "ताज़ा पका खाना खाएँ। पका पपीता ठीक है। शराब, तंबाकू, गुटखा बिल्कुल नहीं।"),

    # ---------------- Exercise ----------------
    _d("ex-walk", "exercise", "Safe activity in pregnancy",
       "Aim for about 30 minutes of gentle activity most days, such as walking, light housework and stretching. Avoid heavy lifting, jumping and lying flat on your back for long after the first trimester. Sleep on your left side in later pregnancy.",
       "गर्भावस्था में व्यायाम", "रोज़ लगभग 30 मिनट टहलें या हल्का काम करें। भारी सामान न उठाएँ। बाद के महीनों में बाईं करवट सोएँ।"),
    _d("ex-yoga", "exercise", "Prenatal yoga and breathing",
       "Gentle prenatal yoga (butterfly pose, cat–cow, supported squats) and slow breathing (anulom-vilom) help with back pain, sleep and stress. Avoid poses on the belly, deep twists and holding your breath. Learn from a trained instructor or your ASHA.",
       "प्रसव-पूर्व योग", "तितली आसन, धीमी साँस (अनुलोम-विलोम) कमर दर्द और तनाव में मदद करते हैं। पेट के बल आसन न करें।"),
    _d("ex-kegel", "exercise", "Pelvic floor (Kegel) exercises",
       "Squeeze the muscles you use to stop urine, hold for 5 seconds, relax for 5 seconds; repeat 10 times, three times a day. This helps with leaking urine and recovery after delivery.",
       "पेल्विक व्यायाम", "पेशाब रोकने वाली मांसपेशियों को 5 सेकंड कसें, फिर ढीला छोड़ें; दिन में 3 बार 10 बार करें।"),
    _d("ex-stop", "exercise", "When to stop exercising",
       "Stop and rest if you feel dizzy, breathless, have chest pain, bleeding, fluid leaking, painful contractions or reduced baby movement, and contact your ASHA or facility.",
       "व्यायाम कब रोकें", "चक्कर, साँस फूलना, खून आना या दर्द हो तो रुकें और आशा दीदी से संपर्क करें।"),

    # ---------------- Mental wellbeing ----------------
    _d("mw-normal", "mental_wellbeing", "Feelings in pregnancy are normal",
       "Mood swings, worry about the baby, money or delivery, feeling lonely or sad, and feeling tired or teary are common in pregnancy. Talking to someone you trust, resting, short walks and keeping a routine can help.",
       "भावनाएँ सामान्य हैं", "गर्भावस्था में मूड बदलना, चिंता या थकान आम है। किसी अपने से बात करें, आराम करें।"),
    _d("mw-depression", "mental_wellbeing", "When sadness needs support",
       "If you feel low, hopeless, unable to enjoy anything, or very anxious for more than two weeks, or have trouble sleeping and eating, this may be perinatal depression or anxiety. It is common and treatable. Tell your ASHA or doctor, or call Tele-MANAS on 14416. If you have thoughts of harming yourself, call 14416 or 108 now.",
       "जब उदासी में मदद चाहिए", "दो हफ़्ते से ज़्यादा उदासी, निराशा या घबराहट हो तो आशा दीदी या डॉक्टर को बताएँ या 14416 पर कॉल करें।"),
    _d("mw-family", "mental_wellbeing", "Support from family",
       "Partners and family can help by sharing housework, coming to ANC visits, making sure she eats and rests, and listening without judging. No one should hit, shout at or threaten a pregnant woman; if you are unsafe, call 181 (Women Helpline) or 112.",
       "परिवार का साथ", "परिवार घर के काम में हाथ बँटाए, जाँच में साथ जाए और बिना टोके सुने। असुरक्षित हों तो 181 या 112 पर कॉल करें।"),
    _d("mw-calm", "mental_wellbeing", "Calming techniques",
       "Try slow breathing: breathe in for 4 counts, hold for 2, breathe out for 6, for five minutes. Listening to music, praying, talking to baby, and writing down worries can also help you feel calmer.",
       "मन शांत करने के तरीके", "धीरे साँस लें: 4 गिनती अंदर, 2 रोकें, 6 बाहर, पाँच मिनट तक।"),

    # ---------------- Schemes ----------------
    _d("sc-pmmvy", "schemes", "Pradhan Mantri Matru Vandana Yojana (PMMVY)",
       "PMMVY gives ₹5,000 for the first child in two instalments, after pregnancy registration and ANC, and after birth registration and the baby's first vaccines. Under PMMVY 2.0, ₹6,000 is given for a second child if it is a girl. Apply through your Anganwadi worker or ASHA with Aadhaar, MCP card and bank account.",
       "प्रधानमंत्री मातृ वंदना योजना", "पहले बच्चे के लिए ₹5,000 दो किस्तों में; दूसरी संतान बेटी हो तो ₹6,000। आंगनवाड़ी या आशा दीदी से आवेदन करें।",
       source="Ministry of Women & Child Development"),
    _d("sc-jsy", "schemes", "Janani Suraksha Yojana (JSY)",
       "JSY gives cash assistance for delivery in a government or accredited hospital: in rural areas of low-performing states, ₹1,400 to the mother (₹700 in high-performing states). Keep your MCP card, Aadhaar and bank details ready; your ASHA helps with the paperwork.",
       "जननी सुरक्षा योजना", "सरकारी अस्पताल में प्रसव पर नकद सहायता (ग्रामीण: ₹1,400 / ₹700)। आशा दीदी कागज़ात में मदद करेंगी।",
       source="National Health Mission"),
    _d("sc-jssk", "schemes", "Janani Shishu Suraksha Karyakram (JSSK)",
       "JSSK entitles every pregnant woman to completely free delivery (including C-section) in public hospitals, free medicines, tests, blood, diet during the stay, and free transport from home to hospital and back (call 102). Sick infants up to one year also get free treatment.",
       "जननी शिशु सुरक्षा कार्यक्रम", "सरकारी अस्पताल में मुफ़्त प्रसव, दवाइयाँ, जाँच, खून, भोजन और 102 से मुफ़्त आना-जाना।",
       source="National Health Mission"),
    _d("sc-pmsma", "schemes", "Pradhan Mantri Surakshit Matritva Abhiyan (PMSMA)",
       "On the 9th of every month, government facilities offer a free ANC check-up by a doctor or specialist for women in the second and third trimesters, including tests and ultrasound where available.",
       "प्रधानमंत्री सुरक्षित मातृत्व अभियान", "हर महीने की 9 तारीख़ को सरकारी अस्पताल में डॉक्टर से मुफ़्त जाँच।",
       source="MoHFW"),
    _d("sc-icds", "schemes", "Anganwadi services (ICDS / Poshan)",
       "Register at your Anganwadi centre to get Take-Home Ration or hot cooked meals, IFA and calcium supplements, health and nutrition counselling, and growth monitoring for your child.",
       "आंगनवाड़ी सेवाएँ", "आंगनवाड़ी में पंजीकरण कर पोषण आहार, आयरन-कैल्शियम और सलाह पाएँ।", source="MWCD"),
    _d("sc-telemanas", "schemes", "Tele-MANAS (14416)",
       "Tele-MANAS is the free, 24×7 national tele-mental-health helpline. Call 14416 or 1-800-891-4416 to talk to a trained counsellor in your language.",
       "टेली-मानस", "14416 पर 24 घंटे मुफ़्त मानसिक स्वास्थ्य सहायता, आपकी भाषा में।", source="MoHFW"),

    # ---------------- Common symptoms ----------------
    _d("sy-dizziness", "symptoms", "Dizziness",
       "Dizziness is common in pregnancy because of hormone changes, low blood sugar, dehydration or anaemia (low haemoglobin). Sit or lie down on your left side, drink water and eat something. Get up slowly. Take your iron tablet daily. If you faint, or have blurred vision, bleeding, chest pain or breathlessness, seek care immediately.",
       "चक्कर आना", "हार्मोन, पानी की कमी या खून की कमी से चक्कर आ सकते हैं। बाईं करवट लेटें, पानी पिएँ, कुछ खाएँ। बेहोशी या खून आए तो तुरंत अस्पताल जाएँ।"),
    _d("sy-swelling", "symptoms", "Swelling in feet",
       "Mild swelling of the feet and ankles, especially in the evening and in the third trimester, is common. Rest with your feet up, avoid standing for long, sleep on your left side and reduce salty snacks. Swelling of the face or hands, or sudden swelling with headache or blurred vision, can mean high BP: get checked the same day.",
       "पैरों में सूजन", "शाम को पैरों में हल्की सूजन आम है; पैर ऊपर रखकर आराम करें। चेहरे/हाथों पर सूजन या सिरदर्द के साथ सूजन हो तो उसी दिन BP जाँच कराएँ।"),
    _d("sy-nausea", "symptoms", "Nausea and vomiting",
       "Morning sickness usually improves after week 12–14. Eat small, dry snacks (roti, biscuits, roasted chana) often, sip water, ginger or lemon water, and avoid oily food. If you cannot keep any food or water down for a day, feel very weak, or pass very little urine, go to the PHC.",
       "उल्टी-मतली", "थोड़ा-थोड़ा सूखा नाश्ता बार-बार लें, अदरक या नींबू पानी पिएँ। कुछ भी न टिके तो PHC जाएँ।"),
    _d("sy-heartburn", "symptoms", "Heartburn and acidity",
       "Eat smaller meals, avoid spicy and fried food, don't lie down right after eating, and raise your head while sleeping. Cold milk or curd can soothe it. Ask before taking antacids, and take them at a different time from iron.",
       "सीने में जलन", "थोड़ा-थोड़ा खाएँ, तला-मसालेदार कम करें, खाने के तुरंत बाद न लेटें।"),
    _d("sy-constipation", "symptoms", "Constipation",
       "Common with iron tablets. Drink more water, eat fibre (whole grains, vegetables, fruit, isabgol if advised) and walk daily. Do not stop your iron tablets.",
       "कब्ज़", "ज़्यादा पानी पिएँ, रेशेदार खाना खाएँ, रोज़ टहलें। आयरन की गोली बंद न करें।"),
    _d("sy-backpain", "symptoms", "Back pain and leg cramps",
       "Back pain is common as baby grows. Keep good posture, avoid lifting heavy things, use a pillow between your knees while sleeping, and try gentle stretching. Leg cramps can be eased by stretching the calf and taking calcium tablets regularly. Back pain with fever, burning urine or regular tightening of the belly needs a check-up.",
       "कमर दर्द और पैरों में ऐंठन", "सही तरीके से बैठें, भारी न उठाएँ, घुटनों के बीच तकिया रखकर सोएँ। कैल्शियम नियमित लें।"),
    _d("sy-tiredness", "symptoms", "Tiredness",
       "Feeling tired is normal, especially in the first and third trimesters, but severe tiredness with breathlessness, pale skin or a racing heart can be anaemia. Rest when you can and get your haemoglobin checked.",
       "थकान", "थकान सामान्य है, पर साँस फूलना या पीलापन हो तो खून (Hb) की जाँच कराएँ।"),
    _d("sy-discharge", "symptoms", "White discharge and urine problems",
       "A thin, white, odourless discharge is normal. Itching, a bad smell, green or yellow colour, or burning while passing urine may be an infection: visit the PHC. Frequent urination is normal, but pain or fever is not.",
       "सफ़ेद पानी और पेशाब की परेशानी", "हल्का सफ़ेद स्राव सामान्य है। खुजली, बदबू या पेशाब में जलन हो तो PHC जाएँ।"),
    _d("sy-sleep", "symptoms", "Sleep problems",
       "Sleep on your left side with a pillow under your belly and between your knees. Avoid tea in the evening, keep a routine and try slow breathing. Ongoing sleeplessness with low mood is worth discussing with your ASHA.",
       "नींद की परेशानी", "बाईं करवट तकिया लगाकर सोएँ, शाम को चाय न पिएँ।"),

    # ---------------- Understanding reports ----------------
    _d("rp-hb", "reports", "Haemoglobin (Hb) in pregnancy",
       "Haemoglobin shows how much oxygen your blood can carry. In pregnancy, Hb of 11 g/dL or above is normal. 10–10.9 is mild anaemia, 7–9.9 is moderate anaemia, and below 7 is severe anaemia, which needs urgent treatment in hospital. Mild and moderate anaemia are treated with daily or extra iron, iron-rich food, and sometimes IV iron advised by a doctor.",
       "हीमोग्लोबिन", "गर्भावस्था में Hb 11 या उससे ज़्यादा सामान्य है। 10–10.9 हल्की, 7–9.9 मध्यम और 7 से कम गंभीर खून की कमी है।"),
    _d("rp-cbc", "reports", "Understanding a CBC report",
       "A Complete Blood Count (CBC) includes haemoglobin, red blood cells (RBC), white blood cells (WBC, normal about 4,000–11,000 per microlitre; slightly higher in pregnancy), platelets (normal about 1.5–4.5 lakh per microlitre), and MCV, which helps tell the type of anaemia. Your doctor reads these together with your symptoms.",
       "CBC रिपोर्ट समझें", "CBC में हीमोग्लोबिन, WBC (4,000–11,000), प्लेटलेट्स (1.5–4.5 लाख) आदि होते हैं।"),
    _d("rp-sugar", "reports", "Blood sugar tests",
       "In India, pregnant women are screened for gestational diabetes with a 75 g glucose test (OGTT) at the first visit and again at 24–28 weeks. A 2-hour value of 140 mg/dL or more suggests gestational diabetes. A fasting value of 92 mg/dL or more is also raised. Raised sugar needs diet changes and a doctor's follow-up.",
       "शुगर की जाँच", "24–28 हफ़्ते पर 75 ग्राम ग्लूकोज़ जाँच होती है। 2 घंटे बाद 140 या ज़्यादा हो तो गर्भावधि मधुमेह हो सकता है।"),
    _d("rp-bp", "reports", "Blood pressure readings",
       "Normal BP in pregnancy is below 140/90 mmHg. 140/90 or above is high and needs a doctor's review within a day or two; 160/110 or above is severe and needs hospital care immediately, especially with headache, blurred vision or swelling.",
       "BP रीडिंग", "140/90 से कम सामान्य है। 140/90 या ज़्यादा पर डॉक्टर को दिखाएँ; 160/110 या ज़्यादा पर तुरंत अस्पताल जाएँ।"),
    _d("rp-urine", "reports", "Urine tests",
       "Urine is tested for protein (albumin) and sugar. Protein in urine with high BP can be a sign of pre-eclampsia. Pus cells or nitrites can mean a urinary infection that needs treatment.",
       "पेशाब की जाँच", "पेशाब में प्रोटीन और हाई BP प्री-एक्लेम्पसिया का संकेत हो सकता है।"),
    _d("rp-ultrasound", "reports", "Ultrasound terms",
       "Common ultrasound terms: GA (gestational age), EDD (expected date of delivery), FHR (fetal heart rate, normally 110–160 beats per minute), EFW (estimated fetal weight), AFI (amniotic fluid index; low is oligohydramnios, high is polyhydramnios), placenta position (low-lying or previa needs follow-up), and presentation (cephalic means head-down).",
       "अल्ट्रासाउंड शब्द", "FHR (धड़कन 110–160), AFI (पानी की मात्रा), प्लेसेंटा की स्थिति, सिर नीचे होना (सेफ़ैलिक)।"),
    _d("rp-thyroid", "reports", "Thyroid (TSH)",
       "TSH checks thyroid function. In pregnancy, a TSH above about 2.5–4 mIU/L (depending on the lab and trimester) may need a doctor's review and thyroid medicine. Take thyroid tablets on an empty stomach, away from iron and calcium.",
       "थायराइड (TSH)", "TSH ज़्यादा हो तो डॉक्टर को दिखाएँ। थायराइड की गोली खाली पेट, आयरन-कैल्शियम से अलग लें।"),
]

# ANC schedule documents
for item in ANC_SCHEDULE:
    DOCUMENTS.append(_d(
        f"anc-{item['visit']}", "anc_schedule", f"{item['label']} (weeks {item['from_week']}–{item['to_week']})",
        f"{item['label']} is due between weeks {item['from_week']} and {item['to_week']}. Checks: "
        + "; ".join(item["checks"]) + ". Bring your MCP card and any previous reports. "
        "Free ANC with a doctor is also available on the 9th of every month (PMSMA).",
        f"ANC जाँच {item['visit']}", "अपना MCP कार्ड और पुरानी रिपोर्ट साथ लाएँ।",
        weeks=(item["from_week"], item["to_week"]),
    ))
DOCUMENTS.append(_d(
    "anc-overview", "anc_schedule", "ANC check-up schedule",
    "Have at least four antenatal check-ups (ANC): before 12 weeks, at 14–26 weeks, at 28–34 weeks, and at 36 weeks to delivery, plus more if your doctor advises. At each visit your BP, weight, haemoglobin, urine and baby's growth and heartbeat are checked.",
    "ANC जाँच का समय", "कम से कम 4 जाँच: 12 हफ़्ते से पहले, 14–26, 28–34 और 36 हफ़्ते के बाद।"))

# Week-by-week guide documents
for wk, (size, size_hi, length, weight, milestone, milestone_hi) in WEEKS.items():
    weight_txt = f" and weighs about {weight} g" if weight else ""
    DOCUMENTS.append(_d(
        f"wk-{wk}", "pregnancy_guide", f"Week {wk} of pregnancy",
        f"At week {wk}, baby is about the size of a {size}, around {length} cm long{weight_txt}. {milestone}",
        f"हफ़्ता {wk}", f"हफ़्ता {wk}: बच्चा {size_hi} जितना बड़ा है। {milestone_hi}",
        weeks=(wk, wk),
    ))

# Larger knowledge base: everyday questions, conditions, birth, postnatal, breastfeeding, newborn and more.
from app.knowledge.documents_more import MORE_CATEGORIES, MORE_DOCUMENTS  # noqa: E402
from app.knowledge.documents_more2 import MORE2_CATEGORIES, MORE2_DOCUMENTS  # noqa: E402

DOCUMENTS.extend(MORE_DOCUMENTS)
DOCUMENTS.extend(MORE2_DOCUMENTS)
CATEGORIES.extend(c for c in MORE_CATEGORIES + MORE2_CATEGORIES if c not in CATEGORIES)
