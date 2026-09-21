# Home page text for techspressionism.com (draft, 2026-09-21)

## 1. Title tag and meta description (Yoast, on the Home page)

**SEO title** (51 characters):
`Techspressionism: Expressionism for the Digital Age`

**Meta description** (160 characters):
`Techspressionism is an artistic approach in which technology is used to express emotional experience, and an international community of artists founded in 2020.`

## 2. Two plain-text sentences directly under the hero (add as a text block, not inside the video or slider)

```html
<p class="ts-intro"><strong>Techspressionism</strong> is an artistic approach in which technology is utilized as a means to express emotional experience. It is also an international, self-identified community of artists, founded in 2020, that gathers in monthly online salons, exhibits in museums and galleries around the world, and keeps a free, searchable <a href="/archive/">video archive</a> of its conversations. <a href="/what-is-techspressionism/">What is Techspressionism?</a></p>
```

## 3. Key facts strip (a short list or a row of five small blocks, each a link)

```html
<ul class="ts-facts">
<li><a href="/history/"><strong>Founded 2020</strong>: the website launched on August 22, 2020</a></li>
<li><a href="/artists/"><strong>460 artists</strong> from 50 countries in the Visual Artists Index</a></li>
<li><a href="/salon/"><strong>110+ salons</strong> since September 2020</a></li>
<li><a href="/exhibitions/"><strong>Exhibitions</strong> in New York, Los Angeles, Chicago, Cape Cod and Uzbekistan</a></li>
<li><a href="/archive/"><strong>147 recordings</strong>, transcribed and searchable in the Video Archive</a></li>
</ul>
```

Numbers to confirm before publishing (they change): 460 artists / 50 countries (from the artist index as exported on September 20, 2026; the archive's own count is in `data/artists.json`), "110+" salons, 147 recordings (the archive builds this number itself).

## 4. One wording change on the home page

Replace "Techspressionism is different from other art movements because membership is based on self-identification" with:

`Techspressionism is different from many art movements because membership is based on self-identification.`

## 5. Links to add

- The MOVEMENT button on History (and any page that has it) points to /movement/, which is a 404: point it to /what-is-techspressionism/.
- Footer, every page: "What is Techspressionism?" -> /what-is-techspressionism/.
- History and Manifesto: a "See also: What is Techspressionism?" line.
