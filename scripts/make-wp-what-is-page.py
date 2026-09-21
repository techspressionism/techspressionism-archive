#!/usr/bin/env python3
"""Write the paste-ready WordPress page "What is Techspressionism?" -> wordpress/what-is-techspressionism.html

    python3 scripts/make-wp-what-is-page.py

Every fact comes from techspressionism.com/history and /manifesto (Colin's own pages) and from the archive's own counts. The visible
question-and-answer list and the FAQPage structured data are made from the SAME list below, so they always agree. Edit the text here,
run the script, and paste the file into the page's Text/HTML editor (or a Custom HTML block).
"""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "wordpress" / "what-is-techspressionism.html"
SITE = "https://techspressionism.com"
PAGE = f"{SITE}/about/"          # the existing "What is Techspressionism?" page (it was called Movement)
DEFINITION = "An artistic approach in which technology is utilized as a means to express emotional experience."

corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
N_REC = len(corpus)
HOURS = round(sum(float(x.get("duration_seconds") or 0) for x in corpus) / 3600)

# (question, answer as HTML with links, answer as plain text for the structured data)
FAQ = [
    ("What is Techspressionism?",
     f"Techspressionism is “{DEFINITION[0].lower() + DEFINITION[1:-1]}.” The word also names an international community of artists who work with technology, "
     f"founded in 2020, that meets in online salons, publishes exhibitions and keeps a public index of its artists.",
     f"Techspressionism is “{DEFINITION[0].lower() + DEFINITION[1:-1]}.” The word also names an international community of artists who work with technology, "
     "founded in 2020, that meets in online salons, publishes exhibitions and keeps a public index of its artists."),
    ("Who coined the term Techspressionism, and when?",
     "Artist Colin Goldberg used it in 2011 as the title of a solo exhibition at 4 North Main Gallery in Southampton, New York; the catalog carried a foreword "
     "by the critic and curator Helen Harrison. It first described Goldberg’s “intersection of technology and abstraction.” In 2018 the artist Oz Van Rosen "
     "independently used the term for her glitch art and digitally manipulated photography. See the <a href=\"/history/\">full history</a>.",
     "Artist Colin Goldberg used it in 2011 as the title of a solo exhibition at 4 North Main Gallery in Southampton, New York; the catalog carried a foreword "
     "by the critic and curator Helen Harrison. It first described Goldberg’s “intersection of technology and abstraction.” In 2018 the artist Oz Van Rosen "
     "independently used the term for her glitch art and digitally manipulated photography."),
    ("Who founded the Techspressionist community?",
     "In August 2020 Goldberg and Oz Van Rosen set out to form an artist group, starting on Instagram. The website launched on August 22, 2020, with the artist "
     "Steve Miller. The first Techspressionist Virtual Salon took place on Zoom on September 1, 2020, with five people: Goldberg, Steve Miller, Oz Van Rosen, the artist "
     "and theorist Patrick Lichty, and Helen Harrison, who became the group’s advisor. That core group agreed on the current definition that day and effectively became "
     "the founders of the movement.",
     "In August 2020 Goldberg and Oz Van Rosen set out to form an artist group, starting on Instagram. The website launched on August 22, 2020, with the artist "
     "Steve Miller. The first Techspressionist Virtual Salon took place on Zoom on September 1, 2020, with five people: Goldberg, Steve Miller, Oz Van Rosen, the artist "
     "and theorist Patrick Lichty, and Helen Harrison, who became the group’s advisor. That core group agreed on the current definition that day and effectively became "
     "the founders of the movement."),
    ("Is Techspressionism an art movement?",
     "Its founders describe it as one, and a 2014 WIRED article referred to it as an art movement. The <a href=\"/manifesto/\">Techspressionist Manifesto</a> "
     "describes it as a social sculpture that is open to all artists. The definition concerns purpose, expressing emotional experience through technology, "
     "not a particular medium or look.",
     "Its founders describe it as one, and a 2014 WIRED article referred to it as an art movement. The Techspressionist Manifesto describes it as a social "
     "sculpture that is open to all artists. The definition concerns purpose, expressing emotional experience through technology, not a particular medium or look."),
    ("How is Techspressionism related to Expressionism?",
     "The 2014 definition was built from the Oxford Dictionary definitions of Expressionism and technology: an artistic style in which technology is used to express "
     "emotional experience rather than impressions of the external world. In 2020 the founding group revised it to today’s wording: Harrison suggested “approach” "
     "in place of “style,” and Van Rosen suggested removing “impressions of the external world.”",
     "The 2014 definition was built from the Oxford Dictionary definitions of Expressionism and technology: an artistic style in which technology is used to express "
     "emotional experience rather than impressions of the external world. In 2020 the founding group revised it to today’s wording: Harrison suggested approach "
     "in place of style, and Van Rosen suggested removing impressions of the external world."),
    ("What is the Techspressionist Manifesto?",
     "A short statement of principles that Colin Goldberg first published on Medium in September 2014. It holds that technology is a continuum as old as humanity and "
     "a natural extension of us, that work is best described as computer-assisted, that coding is an art, and that Techspressionism is inclusive and open to all "
     "artists. Patrick Lichty revised it in 2020 (v2.0), Renata Janiszewska added item 11 in 2021, and Goldberg restored the 2014 text, keeping item 11, in 2025 (v3.0). "
     "<a href=\"/manifesto/\">Read the manifesto</a>.",
     "A short statement of principles that Colin Goldberg first published on Medium in September 2014. It holds that technology is a continuum as old as humanity and "
     "a natural extension of us, that work is best described as computer-assisted, that coding is an art, and that Techspressionism is inclusive and open to all "
     "artists. Patrick Lichty revised it in 2020 (v2.0), Renata Janiszewska added item 11 in 2021, and Goldberg restored the 2014 text, keeping item 11, in 2025 (v3.0)."),
    ("What are the Techspressionist Salons?",
     "Online meetups on Zoom, begun on September 1, 2020 as a modern counterpart to the Surrealist salons of the 1920s, where artists meet to share work and discuss "
     "ideas. Recordings have been published on the Techspressionism YouTube channel since January 5, 2021, alongside an artist interview series, roundtable "
     "discussions and presentations. See the <a href=\"/salon/\">salons</a>, <a href=\"/interviews/\">interviews</a> and <a href=\"/roundtable/\">roundtables</a>.",
     "Online meetups on Zoom, begun on September 1, 2020 as a modern counterpart to the Surrealist salons of the 1920s, where artists meet to share work and discuss "
     "ideas. Recordings have been published on the Techspressionism YouTube channel since January 5, 2021, alongside an artist interview series, roundtable "
     "discussions and presentations."),
    ("Where can I watch or search the recordings?",
     f"The <a href=\"/archive/\">Techspressionism Video Archive</a> has searchable, timestamped transcripts of {N_REC} recordings ({HOURS} hours), each linked to "
     "the exact moment in the YouTube video, plus a page for every artist heard or named. Transcripts are machine-generated and should be checked against the "
     "recording before quoting. <a href=\"/archive/about/\">About the archive and how to cite it</a>.",
     f"The Techspressionism Video Archive has searchable, timestamped transcripts of {N_REC} recordings ({HOURS} hours), each linked to "
     "the exact moment in the YouTube video, plus a page for every artist heard or named. Transcripts are machine-generated and should be checked against the "
     "recording before quoting."),
    ("What is the Techspressionist Visual Artists Index?",
     "A curated selection of artists, started in October 2020, who were found through their use of the hashtag #techspressionism on Instagram. The first artist added "
     "was Markos Pechlivanos, and it has grown into an international list. Browse it on the <a href=\"/artists/\">Artists</a> page.",
     "A curated selection of artists, started in October 2020, who were found through their use of the hashtag #techspressionism on Instagram. The first artist added "
     "was Markos Pechlivanos, and it has grown into an international list."),
    ("What Techspressionist exhibitions have there been?",
     "They include Techspressionism 2021 (online, and the Techspressionist pavilion at The Wrong Biennale no. 5), Techspressionism: Digital and Beyond at "
     "Southampton Arts Center (2022), Hello Brooklyn at the Kingsborough Art Museum (2024), Hello Chelsea at the Hudson Guild Gallery (2025), Hello Uzbekistan "
     "(2025–26) and Techspressionism 2026: Los Angeles and Beyond at the Los Angeles Center for Digital Art. See all <a href=\"/exhibitions/\">exhibitions</a>.",
     "They include Techspressionism 2021 (online, and the Techspressionist pavilion at The Wrong Biennale no. 5), Techspressionism: Digital and Beyond at "
     "Southampton Arts Center (2022), Hello Brooklyn at the Kingsborough Art Museum (2024), Hello Chelsea at the Hudson Guild Gallery (2025), Hello Uzbekistan "
     "(2025–26) and Techspressionism 2026: Los Angeles and Beyond at the Los Angeles Center for Digital Art."),
    ("What are Techspressionist nodes?",
     "Regional groups within the community. The first, Techspressionism.IR, launched in October 2021 for artists of Iran; nodes for France, Germany, Canada and Brazil "
     "followed in April 2022. See the <a href=\"/nodes/\">nodes</a>.",
     "Regional groups within the community. The first, Techspressionism.IR, launched in October 2021 for artists of Iran; nodes for France, Germany, Canada and Brazil "
     "followed in April 2022."),
    ("How can I take part?",
     "Artists who use technology to express emotional experience are welcome; the Manifesto states that Techspressionism is open to all artists. The "
     "<a href=\"/join/\">Join</a> page explains how to get involved, and the community also meets through weekly co-working sessions and the "
     "<a href=\"/loop/\">Loop Art Critique</a> collaboration.",
     "Artists who use technology to express emotional experience are welcome; the Manifesto states that Techspressionism is open to all artists. The "
     "Join page explains how to get involved, and the community also meets through weekly co-working sessions and the Loop Art Critique collaboration."),
]

TIMELINE = [
    ("2011", "Colin Goldberg uses “Techspressionism” as the title of a solo exhibition at 4 North Main Gallery, Southampton, NY."),
    ("2014", "The first Techspressionist Manifesto is published on Medium; WIRED refers to Techspressionism as an art movement."),
    ("2020", "The website launches (August 22) and the first Techspressionist Virtual Salon is held on Zoom (September 1) with Colin Goldberg, Steve Miller, Oz Van Rosen, Patrick Lichty and Helen Harrison. The Visual Artists Index begins in October."),
    ("2021", "The Techspressionism YouTube channel launches (January 5). Techspressionism 2021 opens (October 26), the Techspressionist pavilion at The Wrong Biennale no. 5."),
    ("2022", "Techspressionism: Digital and Beyond opens at Southampton Arts Center (April 21), the first physical group exhibition of Techspressionist artworks."),
    ("2024", "“Hello Brooklyn” // Techspressionism 2024 opens at the Kingsborough Art Museum, the first museum exhibition of Techspressionist artworks (August 7)."),
    ("2025", "“Hello Chelsea” (April 30), Techspressionism in LA (August 7) and “Hello Uzbekistan” at CAMUZ (September 25)."),
    ("2026", "Techspressionism 2026: Los Angeles and Beyond opens at the Los Angeles Center for Digital Art (July); the Loop Art Critique collaboration begins (July 27)."),
]

DESC = ("Techspressionism is an artistic approach in which technology is used to express emotional experience, and an international community of artists "
        "founded in 2020. Definition, history, manifesto, exhibitions and FAQ.")


SPALTER = [
    "Techspressionism is introduced as a new art-historical term to describe fine artists using digital technology to convey subjective, emotional content.",
    "Techspressionism distinguishes expressive fine art from such genres as “digital art,” which can include animated movies, and video games, as well as from “new media” works that do not embody convincing artistic intent.",
    "The subjective lens of the individual artist (rather than the product of a corporate studio) is what connects Techspressionism to its predecessor, Expressionism. Expressionists presented the world from a subjective perspective, distorting it radically in order to evoke moods or ideas, seeking to express their emotional experience rather than physical reality.",
    "A core group of artists have begun working together to develop momentum for the adoption of the term Techspressionism into common usage. We meet biweekly at our Techspressionist Salon artist meetups on Zoom to discuss art and technology. Further information on the origins of the term Techspressionism is available <a href=\"/history/\">here</a>.",
    "The <a href=\"/manifesto/\">Techspressionist Manifesto</a>, a document that draws inspiration from artistic manifestos of the past (including Marinetti’s Futurist Manifesto and Breton’s Surrealist Manifesto) is an open-ended document subject to ongoing revisions from group members.",
    "We encourage artists who identify with the approach of using technology as a means to express emotional experience to self-identify as Techspressionists by including the hashtag #techspressionism on Instagram, Twitter, and other social media platforms.",
    "We are actively reviewing images using the hashtag on Instagram and Twitter and reposting a curated selection of these works. Artists whose work is reposted are invited to be included in an online index of Techspressionist visual artists.",
]

VOICES = [
    "<a href=\"/roundtable/\">Techspressionism: Curators in Conversation with Christiane Paul and Helen A. Harrison</a> is the first of a series of Roundtable Discussions created by Techspressionist artists. This conversation is a discussion focusing of Techspressionism as it relates to art-historical movements of the past as well as to digital art at large.",
    "Christiane Paul is Professor in the School of Media Studies at The New School, as well as Curator of Digital Art at the Whitney Museum of American Art. She is the recipient of the Thoma Foundation’s 2016 Arts Writing Award in Digital Art, and her books are A Companion to Digital Art (Blackwell-Wiley, May 2016); Digital Art (Thames and Hudson, 2003, 2008, 2015, 2023); Context Providers – Conditions of Meaning in Media Arts (Intellect, 2011; Chinese edition, 2012); and New Media in the White Cube and Beyond (UC Press, 2008). At the Whitney Museum she curated exhibitions including Programmed: Rules, Codes, and Choreographies in Art 1965 – 2018 (2018/19), Cory Arcangel: Pro Tools (2011) and Profiling (2007), and is responsible for artport, the museum’s portal to Internet art.",
    "Helen A. Harrison, a former New York Times art critic and NPR arts commentator, is the director of the Pollock-Krasner House and Study Center in East Hampton, New York. A specialist in modern American art, she has been the curator of the Parrish Art Museum and Guild Hall Museum and a guest curator at the Queens Museum. Her books include Hamptons Bohemia: Two Centuries of Artists and Writers on the Beach, monographs on Jackson Pollock and Larry Rivers, and three mystery novels set in the New York art world.",
    "Bronx-born artist Colin Goldberg’s work explores the relationship between technology and personal expression. His studio practice bridges multiple disciplines, notably painting and digital media. Goldberg first used the term Techspressionism as the title for a solo exhibition in Southampton NY in 2011, and curated the first large-scale group exhibition of Techspressionist works, Techspressionism: Digital and Beyond at Southampton Arts Center (Southampton NY, 2022).",
]


def build():
    faq_html = "".join(f"<h3>{html.escape(q)}</h3>\n<p>{a}</p>\n" for q, a, _ in FAQ)
    time_html = "".join(f"<li><strong>{y}</strong>: {html.escape(t)}</li>\n" for y, t in TIMELINE)
    spalter_html = "".join(f"<p>{t}</p>\n" for t in SPALTER)
    voices_html = "".join(f"<p>{t}</p>\n" for t in VOICES)
    graph = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebPage", "@id": PAGE + "#webpage", "url": PAGE, "name": "What is Techspressionism?", "description": DESC, "inLanguage": "en",
         "about": {"@id": PAGE + "#term"}, "isPartOf": {"@id": SITE + "/#website"}, "dateModified": "2026-09-21",
         "publisher": {"@id": SITE + "/#organization"}},
        {"@type": "DefinedTerm", "@id": PAGE + "#term", "name": "Techspressionism", "description": DEFINITION, "url": PAGE},
        {"@type": "Organization", "@id": SITE + "/#organization", "name": "Techspressionism", "url": SITE + "/", "foundingDate": "2020-08-22",
         "description": "An international community of artists who use technology as a means to express emotional experience.",
         "founder": [{"@type": "Person", "name": n} for n in ("Colin Goldberg", "Steve Miller", "Oz Van Rosen", "Patrick Lichty")]},
        {"@type": "FAQPage", "@id": PAGE + "#faq", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": text}} for q, _, text in FAQ]},
    ]}
    return f"""<!-- Page: What is Techspressionism?   Address: {PAGE}  (the existing page, formerly Movement)
     SEO title: What is Techspressionism? Definition, history and manifesto | Techspressionism
     Meta description: {html.escape(DESC)}
     WHAT THIS IS: the new content of the page's text, in this order: a short definition, the original introduction by Anne Morgan Spalter (January 2021, her text
     unchanged except two typos: Twiter -> Twitter, a open-ended -> an open-ended), the origin, key dates, the people, and a FAQ with structured data.
     NOT INCLUDED (keep them in the page builder as they are, or move them as described in the instructions): the MANIFESTO / ARTISTS / EXHIBITIONS / SALON button row,
     the Instagram feed, and the "Art in Focus" talks with their videos (move those to their own page).
     Generated by scripts/make-wp-what-is-page.py -->
<p class="tva-lead"><strong>Techspressionism</strong> is “{html.escape(DEFINITION[0].lower() + DEFINITION[1:-1])}.” The word also names an international community of artists who work with technology,
founded in 2020, that meets in online salons, stages exhibitions in museums and galleries, and keeps a public index of its artists.</p>

<h2>Definition</h2>
<blockquote><p>{html.escape(DEFINITION)}</p></blockquote>
<p><strong>techspressionism</strong> /tek-spresh-uh-niz-uhm/</p>
<p>Root words (source: Oxford Dictionaries):</p>
<ul>
<li><strong>expressionism:</strong> A style of painting, music, or drama in which the artist or writer seeks to express emotional experience rather than impressions of the external world.</li>
<li><strong>technology:</strong> The application of scientific knowledge for practical purposes, especially in industry.</li>
</ul>
<p>This is the wording agreed by the founding group at the first Techspressionist Virtual Salon on September 1, 2020. It is deliberately about purpose,
not about a particular medium, tool or visual style. The <a href="/manifesto/">Techspressionist Manifesto</a> sets out the ideas behind it.</p>

<h2>The original introduction, by Anne Morgan Spalter (January 2021)</h2>
<p><em>Anne Morgan Spalter is a digital mixed-media artist and academic pioneer who founded the original digital fine arts courses at Brown University and the Rhode Island School of Design in the 1990s and wrote the textbook The Computer in the Visual Arts (Addison-Wesley, 1999).</em></p>
<blockquote>
{spalter_html}<p>– Anne Morgan Spalter, January 2021, Brattleboro, VT, USA.</p>
</blockquote>

<h2>Origin of the term</h2>
<p>Colin Goldberg used the word in 2011 as the title of a solo exhibition at 4 North Main Gallery in Southampton, New York. A 2014 WIRED article referred to
Techspressionism as an art movement, and the same year Goldberg published the first manifesto. In 2020 Goldberg, Steve Miller, Oz Van Rosen and Patrick Lichty, with
Helen Harrison as advisor, formed the artist group that became the Techspressionist community. The <a href="/history/">History</a> page has the full record.</p>

<h2>Key dates</h2>
<ul>
{time_html}</ul>

<h2>Voices</h2>
{voices_html}
<h2>Explore</h2>
<ul>
<li><a href="/archive/">Video Archive</a>: searchable, timestamped transcripts of {N_REC} salons, artist interviews, roundtables and presentations.</li>
<li><a href="/artists/">Artists</a>: the Techspressionist Visual Artists Index.</li>
<li><a href="/exhibitions/">Exhibitions</a> and <a href="/press/">press</a>.</li>
<li><a href="/salon/">Salons</a>, <a href="/interviews/">interviews</a> and <a href="/roundtable/">roundtables</a>.</li>
<li><a href="/join/">Join</a> the community.</li>
</ul>

<h2>Frequently asked questions</h2>
{faq_html}
<p><em>Last updated September 21, 2026. Sources: the <a href="/history/">History</a> and <a href="/manifesto/">Manifesto</a> pages of this site.</em></p>

<script type="application/ld+json">
{json.dumps(graph, ensure_ascii=False, indent=1)}
</script>
"""


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size:,} bytes)")
