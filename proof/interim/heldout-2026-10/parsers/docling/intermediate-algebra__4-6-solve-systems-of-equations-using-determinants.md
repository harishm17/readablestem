## 4.6 Solve Systems of Equations Using Determinants

## Learning Objectives

By the end of this section, you will be able to:

- Evaluate the determinant of a 2 × 2 matrix
- Evaluate the determinant of a 3 × 3 matrix
- Use Cramer's Rule to solve systems of equations
- Solve applications using determinants

## Be Prepared 4.6

Before you get started, take this readiness quiz.

1. Simplify: 5(-2) - (-4)(1) . If you missed this problem, review Example 1.20.
2. Simplify: -3(8 - 10) + (-2)(6 - 3) - 4(-3 - (-4)) .

If you missed this problem, review Example 1.19.

3. Simplify: -12 -8 .

If you missed this problem, review Example 1.18.

In this section we will learn of another method to solve systems of linear equations called Cramer's rule. Before we can begin to use the rule, we need to learn some new definitions and notation.

## Evaluate the Determinant of a 2 × 2 Matrix

If a matrix has the same number of rows and columns, we call it a square matrix. Each square matrix has a real number associated with it called its determinant. To find the determinant of the square matrix [ 𝑎 𝑏 𝑐 𝑑 ], we first write it as | 𝑎 𝑏 𝑐 𝑑 | . To get the real number value of the determinate we subtract the products of the diagonals, as shown.

![Image](data:image/png;base64,omitted)

<!-- formula-not-decoded -->

## Determinant

The determinant of any square matrix [ 𝑎 𝑏 𝑐 𝑑 ], where a, b, c, and d are real numbers, is

<!-- formula-not-decoded -->

## Example 4.45

<!-- formula-not-decoded -->

## Solution

ⓐ

| Write the determinant.                  |
|-----------------------------------------|
| Subtract the products of the diagonals. |
| Simplify.                               |
| Simplify.                               |

ⓑ

| Write the determinant.                  |
|-----------------------------------------|
| Subtract the products of the diagonals. |
| Simplify.                               |
| Simplify.                               |

## Try It 4.89

<!-- formula-not-decoded -->

## Try It 4.90

<!-- formula-not-decoded -->

## Evaluate the Determinant of a 3 × 3 Matrix

To evaluate the determinant of a 3 × 3 matrix, we have to be able to evaluate the minor of an entry in the determinant. The minor of an entry is the 2 × 2 determinant found by eliminating the row and column in the 3 × 3 determinant that contains the entry.

## Minor of an entry in 3 × 3 a Determinant

The minor of an entry in a 3 × 3 determinant is the 2 × 2 determinant found by eliminating the row and column in the 3 × 3 determinant that contains the entry.

To find the minor of entry 𝑎1 , we eliminate the row and column which contain it. So we eliminate the first row and first column. Then we write the 2 × 2 determinant that remains.

<!-- formula-not-decoded -->

To find the minor of entry 𝑏2 , we eliminate the row and column that contain it. So we eliminate the 2 nd row and 2 nd column. Then we write the 2 × 2 determinant that remains.

<!-- formula-not-decoded -->

## Example 4.46

```
For the determinant | 4 -2 3 1 0 -3 -2 -4 2 |, find and then evaluate the minor of ⓐ 𝑎1 ⓑ 𝑏3 ⓒ 𝑐2 .
```

| Eliminate the row and column that contains 𝑏3 .   |
|---------------------------------------------------|
| Write the 2 × 2 determinant that remains.         |
| Evaluate.                                         |
| Simplify.                                         |

![Image](data:image/png;base64,omitted)

Simplify.

12

## Try It 4.91

<!-- formula-not-decoded -->

## Try It 4.92

```
For the determinant | -2 -1 0 3 0 -1 -1 -2 3 |, find and then evaluate the minor of ⓐ 𝑎2 ⓑ 𝑏3 ⓒ 𝑐2 .
```

We are now ready to evaluate a 3 × 3 determinant. To do this we expand by minors, which allows us to evaluate the 3 × 3 determinant using 2 × 2 determinants-which we already know how to evaluate!

To evaluate a 3 × 3 determinant by expanding by minors along the first row, we use the following pattern:

![Image](data:image/png;base64,omitted)

Remember, to find the minor of an entry we eliminate the row and column that contains the entry.

## Expanding by Minors along the First Row to Evaluate a 3 × 3 Determinant

To evaluate a 3 × 3 determinant by expanding by minors along the first row, the following pattern:

![Image](data:image/png;base64,omitted)

## Example 4.47

<!-- formula-not-decoded -->

## Solution

<!-- formula-not-decoded -->

## Try It 4.93

```
Evaluate the determinant | 3 -2 4 0 -1 -2 2 3 -1 |, by expanding by minors along the first row.
```

## Try It 4.94

```
Evaluate the determinant | 3 -2 -2 2 -1 4 -1 0 -3 |, by expanding by minors along the first row.
```

To evaluate a 3 × 3 determinant we can expand by minors using any row or column. Choosing a row or column other than the first row sometimes makes the work easier.

When we expand by any row or column, we must be careful about the sign of the terms in the expansion. To determine the sign of the terms, we use the following sign pattern chart.

<!-- formula-not-decoded -->

## Sign Pattern

When expanding by minors using a row or column, the sign of the terms in the expansion follow the following pattern.

<!-- formula-not-decoded -->

Notice that the sign pattern in the first row matches the signs between the terms in the expansion by the first row.

![Image](data:image/png;base64,omitted)

Since we can expand by any row or column, how do we decide which row or column to use? Usually we try to pick a row or column that will make our calculation easier. If the determinant contains a 0, using the row or column that contains the 0 will make the calculations easier.

## Example 4.48

```
Evaluate the determinant | 4 -1 -3 3 0 2 5 -4 -3 | by expanding by minors.
```

## Solution

To expand by minors, we look for a row or column that will make our calculations easier. Since 0 is in the second row and second column, expanding by either of those is a good choice. Since the second row has fewer negatives than the second column, we will expand by the second row.

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

-3(3 -12) + 0(-12- (-15))-2(-16- (-5))

−3(-9) + 0 − 2(-11)

27 + 0+ 22

49

Expand using the second row.

Be careful of the signs.

Evaluate each determinant.

Simplify.

Simplify.

Add.

## Try It 4.95

```
Evaluate the determinant | 2 -1 -3 0 3 -4 3 -4 -3 | by expanding by minors.
```

## Try It 4.96

```
Evaluate the determinant | -2 -1 -3 -1 2 2 4 -4 0 | by expanding by minors.
```

## Use Cramer's Rule to Solve Systems of Equations

Cramer's Rule is a method of solving systems of equations using determinants. It can be derived by solving the general form of the systems of equations by elimination. Here we will demonstrate the rule for both systems of two equations with two variables and for systems of three equations with three variables.

Let's start with the systems of two equations with two variables.

## Cramer's Rule for Solving a System of Two Equations

For the system of equations { 𝑎1 𝑥 + 𝑏1 𝑦 = 𝑘1

<!-- formula-not-decoded -->

Notice that to form the determinant D, we use take the coefficients of the variables.

<!-- formula-not-decoded -->

Notice that to form the determinant 𝐷𝑥 and 𝐷𝑦 , we substitute the constants for the coefficients of the variable we are finding.

![Image](data:image/png;base64,omitted)

![Image](data:image/png;base64,omitted)

![Image](data:image/png;base64,omitted)

![Image](data:image/png;base64,omitted)

## Example 4.49

## How to Solve a System of Equations Using Cramer's Rule

<!-- formula-not-decoded -->

## Solution

- Step 1. Evaluate the determinant D, using the coefficients of the variables.

Step 2. Evaluate the determinant D. Use the constants in place of the x coefficients.

We replace the coefficients of x, 2 and 3, with the constants, -4 and -6.

- Step 3. Evaluate the determinant Dy. Use the constants in place of the y coefficients.

We replace the coefficients of y, 1 and 2, with the constants, -4 and -6.

Step 4. Find x and y.

Step 5. Write the solution as an ordered pair.

Step 6. Check that the ordered pair is a solution to both original equations.

Substitute in the values of D, Dx "a pue

The ordered pair is (x, y).

2x+y=-4

3x-2y=-6

D=

2

1

3

-2

D=-4-3

D=-7

=

-4

1

-6

-2

Dx=8-(-6)

Dx = 14

D

y

=

2

-4

3

-6

Dy=-12-(-12)

D D x= x and y = D D

14

-7

=x andy=

-7

x=-2 andy=0

(-2, 0)

Substitute x = −2, y = 0 into both equations and make sure they are both true.

(−2, 0) is the solution to the system.

0

## Try It 4.97

<!-- formula-not-decoded -->

## Try It 4.98

<!-- formula-not-decoded -->

## How To

## Solve a system of two equations using Cramer's rule.

1. Step 1. Evaluate the determinant D, using the coefficients of the variables.
2. Step 2. Evaluate the determinant 𝐷𝑥 . Use the constants in place of the x coefficients.
3. Step 3. Evaluate the determinant 𝐷𝑦 . Use the constants in place of the y coefficients.
4. Step 4. Find x and y. 𝑥 = 𝐷𝑥 𝐷 , 𝑦 = 𝐷𝑦 𝐷
5. Step 5. Write the solution as an ordered pair.
6. Step 6. Check that the ordered pair is a solution to both original equations.

To solve a system of three equations with three variables with Cramer's Rule, we basically do what we did for a system of two equations. However, we now have to solve for three variables to get the solution. The determinants are also going to be 3 × 3 which will make our work more interesting!

## Cramer's Rule for Solving a System of Three Equations

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

## Example 4.50

Solve the system of equations using Cramer's Rule: { 3𝑥 - 5𝑦 + 4𝑧 = 5 5𝑥 + 2𝑦 + 𝑧 = 0 2𝑥 + 3𝑦 - 2𝑧 = 3 .

## Solution

Evaluate the determinant D.

Expand by minors using column 1.

+ +

一

## Be careful of the signs.

+

+|

+1

Evaluate the determinants.

Simplify.

Simplify.

Simplify.

Evaluate the determinant 𝐷𝑥 . Use the constants to replace the coefficients of x.

Expand by minors using column 1.

Evaluate the determinants.

Simplify.

Simplify.

Evaluate the determinant 𝐷𝑦 . Use the constants to replace the coefficients of y.

Expand by minors using column 2.

Be careful of the signs.

+ + + 一 +| |+

Evaluate the determinants.

Simplify.

Simplify.

Simplify.

Evaluate the determinant 𝐷𝑧 . Use the constants to replace the coefficients of z.

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

D = 3(−7) − 5(−2) + 2(−13)

D =-21+10-26

D =-37

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

D =5(−4-3)-0(10-12)+ 3(−5-8) x

= 5(−7) − 0 + 3(−13)

D =-74 x

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

D =−5(-12) + 0 −3(−17)

=60+0+51

Dy =111

<!-- formula-not-decoded -->

| Evaluate the determinants.                                                   |                               |
|------------------------------------------------------------------------------|-------------------------------|
| Simplify.                                                                    |                               |
| Simplify.                                                                    |                               |
| Simplify.                                                                    |                               |
| Find x, y, and z.                                                            |                               |
| Substitute in the values.                                                    |                               |
| Simplify.                                                                    |                               |
| Write the solution as an ordered triple.                                     |                               |
| Check that the ordered triple is a solution to all three original equations. | We leave the check to you.    |
|                                                                              | The solution is (2, -3, -4) . |

## Try It 4.99

<!-- formula-not-decoded -->

## Try It 4.100

<!-- formula-not-decoded -->

Cramer's rule does not work when the value of the D determinant is 0, as this would mean we would be dividing by 0. But when 𝐷 = 0, the system is either inconsistent or dependent.

When the value of 𝐷 = 0 and 𝐷𝑥 ,𝐷𝑦 and 𝐷𝑧 are all zero, the system is consistent and dependent and there are infinitely many solutions.

When the value of 𝐷 = 0 and 𝐷𝑥 ,𝐷𝑦 and 𝐷𝑧 are not all zero, the system is inconsistent and there is no solution.

## Dependent and Inconsistent Systems of Equations

For any system of equations, where the value of the determinant 𝐷 = 0,

𝐕𝐚𝐥𝐮𝐞 𝐨𝐟 𝐝𝐞𝐭𝐞𝐫𝐦𝐢𝐧𝐚𝐧𝐭𝐬

𝐓𝐲𝐩𝐞 𝐨𝐟 𝐬𝐲𝐬𝐭𝐞𝐦

𝐒𝐨𝐥𝐮𝐭𝐢𝐨𝐧

𝐷 = 0 and𝐷𝑥 ,𝐷𝑦 and𝐷𝑧 are all zero consistent and dependent infinitely many solutions

𝐷 = 0 and𝐷𝑥 ,𝐷𝑦 and𝐷𝑧 are not all zero inconsistent no solution

In the next example, we will use the values of the determinants to find the solution of the system.

## Example 4.51

<!-- formula-not-decoded -->

## Solution

Evaluate the determinant 𝐷, using the

coefficients of the variables.

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

We cannot use Cramer's Rule to solve this system. But by looking at the value of the determinants 𝐷𝑥 and 𝐷𝑦 , we can determine whether the system is dependent or inconsistent.

Evaluate the determinant 𝐷𝑥 .

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

Since all the determinants are not zero, the system is inconsistent. There is no solution.

## Try It 4.101

Solve the system of equations using Cramer's rule: { 4𝑥 - 3𝑦 = 8

<!-- formula-not-decoded -->

## Try It 4.102

<!-- formula-not-decoded -->

## Solve Applications using Determinants

An interesting application of determinants allows us to test if points are collinear. Three points (𝑥1 , 𝑦1 ), (𝑥2 , 𝑦2 ) and (𝑥3 , 𝑦3 ) are collinear if and only if the determinant below is zero.

<!-- formula-not-decoded -->

## Test for Collinear Points

Three points (𝑥1 , 𝑦1 ), (𝑥2 , 𝑦2 ) and (𝑥3 , 𝑦3 ) are collinear if and only if

<!-- formula-not-decoded -->

We will use this property in the next example.

## Example 4.52

Determine whether the points (5, -5), (4, -3), and (3, -1) are collinear.

## Solution

Substitute the values into the determinant. (5, -5), (4, -3), and (3, -1)

Evaluate the determinant by expanding by minors using column 3.

Evaluate the determinants.

Simplify.

Simplify.

x1

y{

x2

y2

x3

y3

5

-5

1

1

1

1|

4

ε−

3

-1

1

1

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

0

The value of the determinate is 0, so the points are collinear.

## Try It 4.103

Determine whether the points (3, -2), (5, -3), and (1, -1) are collinear.

## Try It 4.104

Determine whether the points (-4, -1), (-6, 2), and (-2, -4) are collinear.

## Media

Access these online resources for additional instruction and practice with solving systems of linear inequalities by graphing.

- Solving Systems of Linear Inequalities by Graphing
- Systems of Linear Inequalities

## Section 4.6 Exercises

## Practice Makes Perfect

## Evaluate the Determinant of a 2 × 2 Matrix

In the following exercises, evaluate the determinate of each square matrix.

<!-- formula-not-decoded -->

233.

[ -4 8

-3 5 ]

234.

[ -3

0

235.

[ -2

7

5 -4 ]

0 -5 ]

## Evaluate the Determinant of a 3 × 3 Matrix

In the following exercises, find and then evaluate the indicated minors.

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

238.

| 2

-1

2

-3

-4

-1

0

-2 |

-3

Find the minor ⓐ 𝑎2 ⓑ 𝑏2 ⓒ 𝑐2

<!-- formula-not-decoded -->

In the following exercises, evaluate each determinant by expanding by minors along the first row.

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

242.

| -2

5

-1

-6

-3

-4

2

7

0 |

```
243. | 1 3 -2 5 -6 4 0 -2 -1 |
```

In the following exercises, evaluate each determinant by expanding by minors.

```
244. | -5 -1 -4 4 0 -3 2 -2 6 |
```

```
245. | 4 -1 3 3 -2 2 -1 0 4 |
```

```
246. | 3 5 4 -1 3 0 -2 6 1 |
```

```
247. | 2 -4 -3 5 -1 -4 3 2 0 |
```

## Use Cramer's Rule to Solve Systems of Equations

In the following exercises, solve each system of equations using Cramer's Rule.

```
248. { -2𝑥 + 3𝑦 = 3 𝑥 + 3𝑦 = 12
```

```
249. { 𝑥 - 2𝑦 = -5 2𝑥 - 3𝑦 = -4
```

250.

{ 𝑥 - 3𝑦 = -9

2𝑥 + 5𝑦 = 4

251. { 2𝑥 + 𝑦 = -4

3𝑥 - 2𝑦 = -6

252.

{ 𝑥 - 2𝑦 = -5 2𝑥 - 3𝑦 = -4

253.

{ 𝑥 - 3𝑦 = -9 2𝑥 + 5𝑦 = 4

254.

{ 5𝑥 - 3𝑦 = -1 2𝑥 - 𝑦 = 2

255. { 3𝑥 + 8𝑦 = -3 2𝑥 + 5𝑦 = -3

<!-- formula-not-decoded -->

257. { 4𝑥 - 3𝑦 + 𝑧 = 7 2𝑥 - 5𝑦 - 4𝑧 = 3 3𝑥 - 2𝑦 - 2𝑧 = -7

<!-- formula-not-decoded -->

```
259. { 11𝑥 + 9𝑦 + 2𝑧 = -9 7𝑥 + 5𝑦 + 3𝑧 = -7 4𝑥 + 3𝑦 + 𝑧 = -3
```

```
260. { 𝑥 + 2𝑧 = 0 4𝑦 + 3𝑧 = -2 2𝑥 - 5𝑦 = 3
```

```
261. { 2𝑥 + 5𝑦 = 4 3𝑦 - 𝑧 = 3 4𝑥 + 3𝑧 = -3
```

```
262. { 2𝑦 + 3𝑧 = -1 5𝑥 + 3𝑦 = -6 7𝑥 + 𝑧 = 1
```

```
263. { 3𝑥 - 𝑧 = -3 5𝑦 + 2𝑧 = -6 4𝑥 + 3𝑦 = -8
```

264.

{ 2𝑥 + 𝑦 = 3

6𝑥 + 3𝑦 = 9

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

```
269. { 2𝑥 + 3𝑦 + 𝑧 = 12 𝑥 + 𝑦 + 𝑧 = 9 3𝑥 + 4𝑦 + 2𝑧 = 20
```

<!-- formula-not-decoded -->

<!-- formula-not-decoded -->

## Solve Applications Using Determinants

In the following exercises, determine whether the given points are collinear.

```
272. (0, 1), (2, 0), and (-2, 2) .
```

```
273. (0, -5), (-2, -2), and (2, -8) .
```

```
274. (4, -3), (6, -4), and (2, -2) .
```

```
275. (-2, 1), (-4, 4), and (0, -2) .
```

## Writing Exercises

276.

Explain the difference between a square matrix and its determinant. Give an example of each.

277.

Explain what is meant by the minor of an entry in a square matrix.

278.

Explain how to decide which row or column you will use to expand a 3 × 3 determinant.

279.

Explain the steps for solving a system of equations using Cramer's rule.

## Self Check

ⓐ After completing the exercises, use this checklist to evaluate your mastery of the objectives of this section.

| I can...                                         | Confidently   | With some help   | 2,uo I-on get it!   |
|--------------------------------------------------|---------------|------------------|---------------------|
| evaluate the determinant of a 2 × 2 matrix.      |               |                  |                     |
| evaluate the determinant of a 3 × 3 matrix.      |               |                  |                     |
| use Cramer's rule to solve systems of equations. |               |                  |                     |
| solve applications using determinants.           |               |                  |                     |

Licensed under Creative Commons Attribution License (CC BY 4.0). http://creativecommons.org/licenses/by/4.0/

[Original page: https://openstax.org/books/intermediate-algebra/pages/4-6-solve-systems-of-equations-using-determinants](https://openstax.org/books/intermediate-algebra/pages/4-6-solve-systems-of-equations-using-determinants)

Access for free at openstax.org.

Changes: reformatted as a printable page for evaluating ReadableSTEM; image alt text left out of this print; no wording edited.

ReadableSTEM is not affiliated with or endorsed by OpenStax.