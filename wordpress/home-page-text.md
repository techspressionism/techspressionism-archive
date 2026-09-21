# Home page text for techspressionism.com (draft v2, 2026-09-21)

The current home page already has a lot of text (the 2020 origin story, the definition box, Join, Salons, the Uzbekistan exhibition, Recent Interviews, Co-Working, Digital and Beyond, Roundtables and Helen Harrison's commentary). Keep all of it. The changes below ADD a short answer-first opening, a facts strip and an Archive section, fix a few numbers and links, and add the search title and description.

## 1. Search title and description (Yoast, Home page)

**SEO title** (51 characters): `Techspressionism: Expressionism for the Digital Age`

**Meta description** (160 characters): `Techspressionism is an artistic approach in which technology is used to express emotional experience, and an international community of artists founded in 2020.`

## 2. NEW: two sentences directly under the H1 / hero, ABOVE the "In 2020, at the height of the pandemic..." story

```html
<p class="ts-intro"><strong>Techspressionism</strong> is an artistic approach in which technology is utilized as a means to express emotional experience. It is also an international, self-identified community of artists, founded in 2020, that gathers in monthly online salons, exhibits in museums and galleries around the world, and keeps a free, searchable <a href="/archive/">video archive</a> of its conversations. <a href="/about/">What is Techspressionism?</a></p>
```

## 3. NEW: key facts strip (below the intro, above the story)

```html
<ul class="ts-facts">
<li><a href="/history/"><strong>Founded 2020</strong>: the website launched on August 22, 2020</a></li>
<li><a href="/artists/"><strong>460 artists</strong> from 50 countries in the Visual Artists Index</a></li>
<li><a href="/salon/"><strong>110+ salons</strong> since September 2020</a></li>
<li><a href="/exhibitions/"><strong>Exhibitions</strong> in New York, Los Angeles, Chicago, Cape Cod and Uzbekistan</a></li>
<li><a href="https://www.instagram.com/explore/tags/techspressionism/" rel="noopener"><strong>87,000+ Instagram posts</strong> tagged #techspressionism (September 2026)</a></li>
<li><a href="/archive/"><strong>147 recordings</strong>, transcribed and searchable in the Video Archive</a></li>
</ul>
```

## 4. Numbers to make consistent

- The story text says "more than 350 artists across 45 countries" and "over 85,000 Instagram posts". The artist index export of September 20, 2026 has 460 names from 50 countries, and you said the hashtag is now 87K+. Decide the figures and use them everywhere (intro, strip, story). Suggested: "more than 450 artists across 50 countries" and "over 87,000 posts".
- "110+ salons" is an estimate; 147 recordings / 218 hours is the archive's own count (update when recordings are added).

## 5. NEW: a Video Archive section (after Salons, before the Uzbekistan exhibition)

```html
<h2>VIDEO ARCHIVE</h2>
<p>Every Techspressionist salon, artist interview, roundtable and presentation is transcribed and searchable. Search 147 recordings (218 hours), read along with the video, and cite any passage with its exact timestamp. Transcripts are machine-generated, so check quotations against the video.</p>
<p><a href="/archive/">SEARCH THE ARCHIVE</a></p>
```

In "Recent Interviews", add under each interview: `Read the transcript` -> that interview's page in the archive (for example /archive/interview-0NN/).

## 6. Wording changes

- "Techspressionism is different from other art movements because membership is based on self-identification" -> "... different from many art movements ...".
- Keep "emerging art movement" (already hedged) and the definition box; whether to keep "A 21st-century artistic and social movement." is your decision on the movement wording.

## 7. Keep fresh

- The featured exhibition is Uzbekistan (2025); the most recent is Techspressionism 2026: Los Angeles and Beyond (LACDA, July 2026). Consider featuring it, or a "Latest exhibitions" heading with both.
- Helen A. Harrison's "Critical commentary": make her name a plain-text byline (it does not appear in the page text, so search engines and AI tools cannot attribute it).

## 8. Recommended order of the page

1. Hero reel and H1 "Techspressionism: Expressionism for the Digital Age."
2. NEW two-sentence intro with the "What is Techspressionism?" link
3. NEW facts strip
4. The origin story (three paragraphs, numbers updated)
5. Definition box with the ARTISTS / EXHIBITIONS / SALONS / JOIN buttons
6. "You're a Techspressionist when you say you are" (Join)
7. Salons (Reserve your spot / Watch this salon)
8. NEW Video Archive section
9. Latest exhibition(s)
10. Recent interviews (with transcript links)
11. Virtual co-working
12. Pollock quote and Digital and Beyond
13. Roundtables
14. Critical commentary by Helen A. Harrison (with a byline)
15. Footer buttons (MANIFESTO, EXHIBITIONS, ARTISTS, SALON, JOIN) plus a "What is Techspressionism?" link

## 9. Links to add elsewhere

- The MOVEMENT button on History, Exhibitions and Press points to /movement/, a 404: add the Yoast redirect /movement/ -> /about/.
- Footer on every page: "What is Techspressionism?" -> /about/. Consider an ABOUT item in the flyout menu.
