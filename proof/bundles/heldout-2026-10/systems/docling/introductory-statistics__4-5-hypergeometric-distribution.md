## 4.5 Hypergeometric Distribution

There are five characteristics of a hypergeometric experiment.

1. You take samples from two groups.
2. You are concerned with a group of interest, called the first group.
3. You sample without replacement from the combined groups. For example, you want to choose a softball team from a combined group of 11 men and 13 women. The team consists of ten players.
4. Each pick is not independent, since sampling is without replacement. In the softball example, the probability of picking a woman first is 13 24 . The probability of picking a man second is 11 23 if a woman was picked first. It is 10 23 if a man was picked first. The probability of the second pick depends on what happened in the first pick.
5. You are not dealing with Bernoulli Trials.

The outcomes of a hypergeometric experiment fit a hypergeometric probability distribution. The random variable X = the number of items from the group of interest.

## Example 4.22

## Problem

A candy dish contains 100 jelly beans and 80 gumdrops. Fifty candies are picked at random. What is the probability that 35 of the 50 are gumdrops? The two groups are jelly beans and gumdrops. Since the probability question asks for the probability of picking gumdrops, the group of interest (first group) is gumdrops. The size of the group of interest (first group) is 80. The size of the second group is 100. The size of the sample is 50 (jelly beans or gumdrops). Let X = the number of gumdrops in the sample of 50. X takes on the values x = 0, 1, 2, ..., 50. What is the probability statement written mathematically?

## Solution

P(x = 35)

## Try It 4.22

A bag contains letter tiles. Forty-four of the tiles are vowels, and 56 are consonants. Seven tiles are picked at random. You want to know the probability that four of the seven tiles are vowels. What is the group of interest, the size of the group of interest, and the size of the sample?

## Example 4.23

## Problem

Suppose a shipment of 100 DVD players is known to have ten defective players. An inspector randomly chooses 12 for inspection. He is interested in determining the probability that, among the 12 players, at most two are defective. The two groups are the 90 non-defective DVD players and the 10 defective DVD players. The group of interest (first group) is the defective group because the probability question asks for the probability of at most two defective DVD players. The size of the sample is 12 DVD players. (They may be non-defective or defective.) Let X = the number of defective DVD players in the sample of 12. X takes on the values 0, 1, 2, ..., 10. X may not take on the values 11 or 12. The sample size is 12, but there are only 10 defective DVD players. Write the probability statement mathematically.

## Solution

P(x ≤ 2)

## Try It 4.23

A gross of eggs contains 144 eggs. A particular gross is known to have 12 cracked eggs. An inspector randomly chooses 15 for inspection. She wants to know the probability that, among the 15, at most three are cracked. What is X, and what values does it take on?

## Example 4.24

You are president of an on-campus special events organization. You need a committee of seven students to plan a special birthday party for the president of the college. Your organization consists of 18 women and 15 men. You are interested in the number of men on your committee. If the members of the committee are randomly selected, what is the probability that your committee has more than four men?

This is a hypergeometric problem because you are choosing your committee from two groups (men and women).

## Problem

- a. Are you choosing with or without replacement?
- b. What is the group of interest?
- c. How many are in the group of interest?
- d. How many are in the other group?
- e. Let X = \_\_\_\_\_\_\_\_\_ on the committee. What values does X take on?
- f. The probability question is P(\_\_\_\_\_\_\_).

## Solution

- a. without
- b. the men
- c. 15 men
- d. 18 women
- e. Let X = the number of men on the committee. x = 0, 1, 2, …, 7.
- f. P(x &gt; 4)

| Try It 4.24   | Try It 4.24                                                                                                                                                                                                                                                                                                  |
|---------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|               | A palette has 200 milk cartons. Of the 200 cartons, it is known that ten of them have leaked and cannot be sold. A stock clerk randomly chooses 18 for inspection. He wants to know the probability that among the 18, no more than two are leaking. Give five reasons why this is a hypergeometric problem. |

## Notation for the Hypergeometric: H = Hypergeometric Probability Distribution Function

## X ~ H(r, b, n)

Read this as "X is a random variable with a hypergeometric distribution." The parameters are r, b, and n; r = the size of the group of interest (first group), b = the size of the second group, n = the size of the chosen sample.

## Example 4.25

A school site committee is to be chosen randomly from six men and five women. If the committee consists of four members chosen randomly, what is the probability that two of them are men? How many men do you expect to be on the committee?

Let X = the number of men on the committee of four. The men are the group of interest (first group).

X takes on the values 0, 1, 2, 3, 4, where r = 6, b = 5, and n = 4. X ~ H(6, 5, 4)

Find P(x = 2). P(x = 2) = 0.4545 (calculator or computer)

## NOTE

Currently, the TI-83+ and TI-84 do not have hypergeometric probability functions. There are a number of computer packages, including Microsoft Excel, that do.

The probability that there are two men on the committee is about 0.45.

The graph of X ~ H(6, 5, 4) is:

Figure 4.4

![Image](data:image/png;base64,omitted)

The y-axis contains the probability of X, where X = the number of men on the committee.

You would expect m = 2.18 (about two) men on the committee.

The formula for the mean is 𝜇 = 𝑛𝑟 𝑟 + 𝑏 = (4)(6) 6 + 5 = 2.18

| Try It 4.25   | Try It 4.25                                                                                                                                                                                                                      |
|---------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|               | An intramural basketball team is to be chosen randomly from 15 boys and 12 girls. The team has ten slots. You want to know the probability that eight of the players will be boys. What is the group of interest and the sample? |

"4.5 Hypergeometric Distribution" from Introductory Statistics by Barbara Illowsky, Susan Dean, OpenStax. Licensed under Creative Commons Attribution License (CC BY 4.0). http://creativecommons.org/licenses/by/4.0/ Original page: https://openstax.org/books/introductory-statistics/pages/4-5-hypergeometric-distribution Access for free at openstax.org. Changes: reformatted as a printable page for evaluating a11y-stem; image alt text left out of this print; no wording edited. a11y-stem is not affiliated with or endorsed by OpenStax.