-- Applied to the live project on 27 Oct 2026, decided on #27.
--
-- A cost's figure and the unit it is counted in are two values, because the page shows them as two
-- elements at two sizes ("61,365" at 40px and "lines, in 8 days" beside it at 13px). Folded into
-- one string the snapshot could not put the tail back, since a fact carries no markup.
--
-- schema.sql and seed.sql already describe the result, so a fresh project needs only those two.
-- Each file in this folder is a change the live project has already had, oldest first, so the shape
-- the puller reads has a written history rather than one that lives in a pull request description.

alter table costs add column unit text;

update costs set figure = '61,365', unit = 'lines, in 8 days' where position = 2;
update costs set figure = '28', unit = 'of 28 runs failed' where position = 4;
