# **4.5 Hypergeometric Distribution** 

There are five characteristics of a hypergeometric experiment. 

1. You take samples from **two** groups. 

2. You are concerned with a group of interest, called the first group. 

3. You sample **without replacement** from the combined groups. For example, you want to choose a softball team from a combined group of 11 men and 13 women. The team consists of ten players. 

4. Each pick is **not** independent, since sampling is without replacement. In the softball example, the probability of picking a woman first is 1324<sup>. The probability of picking a man second is</sup> <u>1123</u><sup>if a woman was picked first. It</sup> is 1023<sup>if a man was picked first. The probability of the second pick depends on what happened in the first</sup> pick. 

5. You are **not** dealing with Bernoulli Trials. 

The outcomes of a hypergeometric experiment fit a hypergeometric probability distribution. The random variable _X_ = the number of items from the group of interest. 

## **Example 4.22** 

### **Problem** 

A candy dish contains 100 jelly beans and 80 gumdrops. Fifty candies are picked at random. What is the probability that 35 of the 50 are gumdrops? The two groups are jelly beans and gumdrops. Since the probability question asks for the probability of picking gumdrops, the group of interest (first group) is gumdrops. The size of the group of interest (first group) is 80. The size of the second group is 100. The size of the sample is 50 (jelly beans or gumdrops). Let _X_ = the number of gumdrops in the sample of 50. _X_ takes on the values _x_ = 0, 1, 2, ..., 50. What is the probability statement written mathematically? 

### **Solution** 

_P_ ( _x_ = 35) 

Access for free at openstax.org. Introductory Statistics (OpenStax), CC BY 4.0. Page 1 of 6 

## **Try It 4.22** 

A bag contains letter tiles. Forty-four of the tiles are vowels, and 56 are consonants. Seven tiles are picked at random. You want to know the probability that four of the seven tiles are vowels. What is the group of interest, the size of the group of interest, and the size of the sample? 

## **Example 4.23** 

### **Problem** 

Suppose a shipment of 100 DVD players is known to have ten defective players. An inspector randomly chooses 12 for inspection. He is interested in determining the probability that, among the 12 players, at most two are defective. The two groups are the 90 non-defective DVD players and the 10 defective DVD players. The group of interest (first group) is the defective group because the probability question asks for the probability of at most two defective DVD players. The size of the sample is 12 DVD players. (They may be non-defective or defective.) Let _X_ = the number of defective DVD players in the sample of 12. _X_ takes on the values 0, 1, 2, ..., 10. _X_ may not take on the values 11 or 12. The sample size is 12, but there are only 10 defective DVD players. Write the probability statement mathematically. 

### **Solution** 

_P_ ( _x_ ≤ 2) 

## **Try It 4.23** 

A gross of eggs contains 144 eggs. A particular gross is known to have 12 cracked eggs. An inspector randomly chooses 15 for inspection. She wants to know the probability that, among the 15, at most three are cracked. What is _X_ , and what values does it take on? 

Access for free at openstax.org. Introductory Statistics (OpenStax), CC BY 4.0. Page 2 of 6 

## **Example 4.24** 

You are president of an on-campus special events organization. You need a committee of seven students to plan a special birthday party for the president of the college. Your organization consists of 18 women and 15 men. You are interested in the number of men on your committee. If the members of the committee are randomly selected, what is the probability that your committee has more than four men? 

This is a hypergeometric problem because you are choosing your committee from two groups (men and women). 

### **Problem** 

a. Are you choosing with or without replacement? 

b. What is the group of interest? 

- c. How many are in the group of interest? 

- d. How many are in the other group? 

- e. Let _X_ = _________ on the committee. What values does _X_ take on? 

f. The probability question is _P_ (_______). 

### **Solution** 

a. without b. the men c. 15 men d. 18 women e. Let _X_ = **the number of men** on the committee. _x_ = 0, 1, 2, …, 7. f. _P_ ( _x_ > 4) 

Access for free at openstax.org. Introductory Statistics (OpenStax), CC BY 4.0. Page 3 of 6 

## **Try It 4.24** 

A palette has 200 milk cartons. Of the 200 cartons, it is known that ten of them have leaked and cannot be sold. A stock clerk randomly chooses 18 for inspection. He wants to know the probability that among the 18, no more than two are leaking. Give five reasons why this is a hypergeometric problem. 

## **Notation for the Hypergeometric: H = Hypergeometric Probability Distribution Function** 

_X_ ~ _H_ ( _r_ , _b_ , _n_ ) 

Read this as " _X_ is a random variable with a hypergeometric distribution." The parameters are _r_ , _b_ , and _n_ ; _r_ = the size of the group of interest (first group), _b_ = the size of the second group, _n_ = the size of the chosen sample. 

Access for free at openstax.org. Introductory Statistics (OpenStax), CC BY 4.0. Page 4 of 6 

## **Example 4.25** 

A school site committee is to be chosen randomly from six men and five women. If the committee consists of four members chosen randomly, what is the probability that two of them are men? How many men do you expect to be on the committee? 

Let _X_ = the number of men on the committee of four. The men are the group of interest (first group). 

_X_ takes on the values 0, 1, 2, 3, 4, where _r = 6_ , _b = 5_ , and _n = 4_ . _X ~ H_ (6, 5, 4) 

Find _P_ ( _x_ = 2). _P_ ( _x_ = 2) = 0.4545 (calculator or computer) 

## **NOTE** 

Currently, the TI-83+ and TI-84 do not have hypergeometric probability functions. There are a number of computer packages, including Microsoft Excel, that do. 

The probability that there are two men on the committee is about 0.45. 

The graph of _X_ ~ _H_ (6, 5, 4) is: 



Figure 4.4 

The _y_ -axis contains the probability of _X_ , where _X_ = the number of men on the committee. You would expect _m_ = 2.18 (about two) men on the committee. The formula for the mean is 𝜇= 𝑟+ 𝑏𝑛𝑟<sup>=</sup> <u>(6 + 54)(6)</u><sup>= 2.18</sup> 

Access for free at openstax.org. Introductory Statistics (OpenStax), CC BY 4.0. Page 5 of 6 

## **Try It 4.25** 

An intramural basketball team is to be chosen randomly from 15 boys and 12 girls. The team has ten slots. You want to know the probability that eight of the players will be boys. What is the group of interest and the sample? 

“4.5 Hypergeometric Distribution” from Introductory Statistics by Barbara Illowsky, Susan Dean, OpenStax. 

Licensed under Creative Commons Attribution License (CC BY 4.0). http://creativecommons.org/licenses/by/4.0/ 

Original page: <u>https://openstax.org/books/introductory-statistics/pages/4-5-hypergeometric-distribution</u> 

Access for free at openstax.org. 

Changes: reformatted as a printable page for evaluating a11y-stem; image alt text left out of this print; no wording edited. a11y-stem is not affiliated with or endorsed by OpenStax. 

Access for free at openstax.org. Introductory Statistics (OpenStax), CC BY 4.0. Page 6 of 6 

