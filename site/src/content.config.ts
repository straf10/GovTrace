import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';

const docsDir = pathToFileURL(path.resolve(process.cwd(), '..', 'docs') + path.sep);

const docs = defineCollection({
  loader: glob({ pattern: '{METHODOLOGY,DISCLAIMER}.md', base: docsDir }),
});

export const collections = { docs };
