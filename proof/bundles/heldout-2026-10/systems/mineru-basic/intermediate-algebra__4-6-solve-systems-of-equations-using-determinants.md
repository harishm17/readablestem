## 4.6 Solve Systems of Equations Using Determinants

## Learning Objectives

By the end of this section, you will be able to:

Evaluate the determinant of $\mathsf { a }   2 \times 2$ matrix

Evaluate the determinant of $\mathsf { a }   3 \times 3$ matrix

Use Cramer’s Rule to solve systems of equations

Solve applications using determinants

## Be Prepared 4.6

Before you get started, take this readiness quiz.

1. Simplify: $5 ( - 2 ) - ( - 4 ) ( 1 )$

If you missed this problem, review Example 1.20.

2. Simplify: $-3(8-10)+(-2)(6-3)-4(-3-(-4))$

If you missed this problem, review Example 1.19.

3. Simpli $1 fy: \frac{-12}{-8}$

If you missed this problem, review Example 1.18.

In this section we will learn of another method to solve systems of linear equations called Cramer’s rule. Before we can begin to use the rule, we need to learn some new definitions and notation.

## Evaluate the Determinant of a $2 \times 2$ Matrix

If a matrix has the same number of rows and columns, we call it a square matrix. Each square matrix has a real number associated with it called its determinant. To find the determinant of the square matrix $[ \begin{array} { l l } { \boldsymbol { ^ { a } } } & { \boldsymbol { ^ { b } } } \\ { \boldsymbol { ^ { c } } } & { \boldsymbol { ^ { d } } } \end{array} ] ,$ we first write it as $\mid { \begin{array} { l l } { a } & { b } \\ { c } & { d } \end{array} } \mid$ **.** To get the real number value of the determinate we subtract the products of the diagonals, as shown.

$$
\left[ a \quad b \quad \right] = ad - bc
$$

## Determinant

The determinant of any square matrix $[ \begin{array} { l l } { \boldsymbol { ^ { a } } } & { \boldsymbol { ^ { b } } } \\ { \boldsymbol { ^ { c } } } & { \boldsymbol { ^ { d } } } \end{array} ] ,$ where a, b, c, and d are real numbers, is

$$
\mid { \begin{array} { l l } { a } & { b } \\ { c } & { d } \end{array} } \mid = a d - b c .
$$

## Example 4.45

Evaluate the determinate of ⓐ $[ \begin{array} { c c } { 4 } & { - 2 } \\ { 3 } & { - 1 } \end{array} ] \quad [ \begin{array} { c c } { - 3 } & { - 4 } \\ { - 2 } & { 0 } \end{array} ]$

## Solution

ⓐ

<table><tr><td></td><td><img src="images/page_1_table_image_4_1.jpg"/></td></tr><tr><td>Write the determinant.</td><td><img src="images/page_1_table_image_4_2.jpg"/></td></tr><tr><td>Subtract the products of the diagonals.</td><td> $4(-1)-3(-2)$ </td></tr><tr><td>Simplify.</td><td>-4+6</td></tr><tr><td>Simplify.</td><td>2</td></tr></table>

ⓑ

<table><tr><td></td><td><eq>\begin{bmatrix} -3 &amp; -4 \\ -2 &amp; 0 \end{bmatrix}</e</td><td>q></td></tr><tr><td>Write the determinant.</td><td colspan=2><img src="images/page_1_table_image_6_1.jpg"/></td></tr><tr><td>Subtract the products of the diagonals.</td><td colspan=2>−3(0)-(-2)(-4)</td></tr><tr><td>Simplify.</td><td colspan=2></td></tr><tr><td>Simplify.</td><td colspan=2></td></tr></table>

## Try It 4.89

Evaluate the determinate of ⓐ $\begin{array} { r l r l r } { { \mathrm { ~ I ~ } } _ { 2 } ^ { 5 } } & { { } \; - 3 } \\ { - 4 } \end{array} ] \; \textcircled { b } \; [ \; \begin{matrix} { - 4 } & { } & { - 6 } \\ { 0 } & { } & { 7 } \end{matrix} \; ] .$

Try It 4.90

Evaluate the determinate of ⓐ $[ - 1 3 ] \textcircled { b } [ - 7 - 3 ]$

## Evaluate the Determinant of a $3 \times 3$ Matrix

To evaluate the determinant of a $3 \times 3$ matrix, we have to be able to evaluate the minor of an entry in the determinant. The minor of an entry is the $2 \times 2$ determinant found by eliminating the row and column in the $3 \times 3$ determinant that contains the entry.

## Minor of an entry in $3 \times 3$ a Determinant

The **minor of an entry** in a $3 \times 3$ determinant is the $2 \times 2$ determinant found by eliminating the row and column in the $3 \times 3$ determinant that contains the entry.

To find the minor of entry $a _ { 1 }$ , we eliminate the row and column which contain it. So we eliminate the first row and first column. Then we write the $2 \times 2$ determinant that remains.

$\begin{bmatrix} a_{1} & b_{1} & c_{1} \\ \varphi_{2} & b_{2} & c_{2} \\ \varphi_{3} & b_{3} & c_{3} \end{bmatrix}$ minor of $a_{1} \begin{vmatrix} b_{2} & c_{2} \\ b_{3} & c_{3} \end{vmatrix}$

To find the minor of entry $b _ { 2 }$ , we eliminate the row and column that contain it. So we eliminate the $2 ^ { \mathrm { n d } }$ row and $2 ^ { \mathrm { n d } }$ column. Then we write the $2 \times 2$ determinant that remains.

$\begin{bmatrix} a_{1} & b_{1} & c_{1} \\ a_{2} & b_{2} & c_{2} \\ a_{3} & b_{3} & c_{3} \end{bmatrix}$ minor of $b_{_2} \begin{vmatrix} a_{_1} & c_{_1} \\ a_{_3} & c_{_3} \end{vmatrix}$

## Example 4.46

For the determinant | $\begin{array} { r l r l r l } { { 4 } } & { { } } & { - 2 } & { { } } & { 3 } \\ { 1 } & { { } } & { 0 } & { { } } & { - 3 } \\ { - 2 } & { { } } & { - 4 } & { { } } & { 2 } \end{array}$ **|,** find and then evaluate the minor of $\textcircled{a} a _ { 1 }  \textcircled{b} b _ { 3 } \odot c _ { 2 }$

## Solution

ⓐ

<table><tr><td></td><td></td><td> $\begin{vmatrix} 4 & - 2 & 3 \\ 1 & 0 & - 3 \\ - 2 & - 4 & 2 \end{vmatrix}$ </td></tr><tr><td>Eliminate the row and column that contains $a _ { 1 }$ </td><td colspan=2><img src="images/page_4_table_image_2_1.jpg"/></td></tr><tr><td>Write the $2 \times 2$ determinant that remains.</td><td colspan=2> $\boxed { \mathsf { m i n o r \: o f \: } a _ { _ 1 } \left| \begin{matrix} { 0 } & { - 3 } \\ { - 4 } & { 2 } \\ \end{matrix} \right| }$ </td></tr><tr><td>Evaluate.</td><td colspan=2>0(2)-(-3)(-4)</td></tr><tr><td>Simplify.</td><td colspan=2></td></tr></table>

ⓑ

| Eliminate the row and column that contains $b _ { 3 }$ |  |  |  |
| --- | --- | --- | --- |
| $2 \times 2$ determinant that remains. Write the | $\boxed { \mathsf { m i n o r } \; \mathsf { o f } \; b _ { _ 3 } \left\| \begin{matrix} { 4 } & { 3 } \\ { } & { } \\ { 1 } & { - 3 } \\ \end{matrix} \right\| }$ |  |  |
| Evaluate. | $\frac{\frac{4(-3)-(1)(3)}{-15}}{-15}$ |  |  |
| Simplify. |  |  |  |

ⓒ

<table><tr><td></td><td></td><td><img src="images/page_4_table_image_6_1.jpg"/></td></tr><tr><td>Eliminate the row and column that contains $c _ { 2 }$ </td><td></td><td><img src="images/page_4_table_image_6_2.jpg"/></td></tr><tr><td>Write the $2 \times 2$ determinant that remains.</td><td> $\mathsf { m i n o r } \; \mathsf { o f } \; c _ { _ 2 } \left| \begin{matrix} { \phantom { - } 4 } &</td><td>{ - 2 } \\ { - 2 } & { \phantom { - } 4 } \\ \end{matrix} \right|$ </td></tr><tr><td>Evaluate.</td><td colspan=2>4(4)−(−2)(−2)</td></tr></table>

<div class="docvortex-algorithm" style="white-space: pre-wrap; font-family:monospace;">
For the determinant | $\begin{array} { r l r l r l } { { 1 } } &amp; { { } } &amp; { - 1 } &amp; { { } } &amp; { 4 } \\ { 0 } &amp; { { } } &amp; { 2 } &amp; { { } } &amp; { - 1 } \\ { - 2 } &amp; { { } } &amp; { - 3 } &amp; { { } } &amp; { 3 } \end{array}$ |, find and then evaluate the minor of ⓐ $$a _ { 1 }$ $\textcircled { b } b _ { 2 }$ $\textcircled { c } c _ { 3 }$$
</div>

## Try It 4.92

<div class="docvortex-algorithm" style="white-space: pre-wrap; font-family:monospace;">
For the determinant | $\begin{array} { r l r l r l } { - 2 } &amp; { { } } &amp; { - 1 } &amp; { { } } &amp; { 0 } \\ { 3 } &amp; { { } } &amp; { 0 } &amp; { { } } &amp; { - 1 } \\ { - 1 } &amp; { { } } &amp; { - 2 } &amp; { { } } &amp; { 3 } \end{array}$ |, find and then evaluate the minor of $\textcircled{a} a _ { 2 } \; \textcircled{b} b _ { 3 } \; \textcircled{c} c _ { 2 }$
</div>

We are now ready to evaluate a $3 \times 3$ determinant. To do this we expand by minors, which allows us to evaluate the $3 \times 3$ determinant using $2 \times 2$ determinants—which we already know how to evaluate!

To evaluate a $3 \times 3$ determinant by expanding by minors along the first row, we use the following pattern:

$$
\begin{vmatrix}a_{_1} & b_{_1} & c_{_1} \\a_{_2} & b_{_2} & c_{_2} \\a_{_3} & b_{_3} & c_{_3}\end{vmatrix}= a_{_1} \begin{vmatrix}b_{_2} & c_{_2} \\b_{_3} & c_{_3} \\\end{vmatrix}- b_{_1} \begin{vmatrix}a_{_2} & c_{_2} \\a_{_3} & c_{_3} \\\end{vmatrix}+ c_{_1} \begin{vmatrix}a_{_2} & b_{_2} \\a_{_3} & b_{_3} \\\end{vmatrix}
$$

Remember, to find the minor of an entry we eliminate the row and column that contains the entry.

## Expanding by Minors along the First Row to Evaluate a $3 \times 3$ Determinant

To evaluate a $3 \times 3$ determinant by **expanding by minors along the first row**, the following pattern:

$$
\begin{vmatrix}a_{_1} & b_{_1} & c_{_1} \\a_{_2} & b_{_2} & c_{_2} \\a_{_3} & b_{_3} & c_{_3}\end{vmatrix}= a_{_1} \begin{vmatrix}b_{_2} & c_{_2} \\b_{_3} & c_{_3} \\\hline\end{vmatrix}- b_{_1} \begin{vmatrix}a_{_2} & c_{_2} \\a_{_3} & c_{_3} \\\hline\end{vmatrix}+ c_{_1} \begin{vmatrix}a_{_2} & b_{_2} \\a_{_3} & b_{_3} \\\hline\end{vmatrix}
$$

Example 4.47

$$
\begin{array} { r l r l r l } { { 2 } } & { { } } & { - 3 } & { { } } & { - 1 } \\ { { 3 } } & { { } } & { 2 } & { { } } & { 0 } \\ { - 1 } & { { } } & { - 1 } & { { } } & { - 2 } \end{array}
$$

|  |  |
| --- | --- |
| Expand by minors along the first row |  |
| Evaluate each determinant. |  |
| Simplify. |  |
| Simplify. |  |
| Simplify. |  |

Try It 4.93

Try It 4.94

|  |  |  |  |
| --- | --- | --- | --- |
| Evaluate the determinant \| |  | $\begin{array} { r l r l r l } { { 3 } } & { { } } & { - 2 } & { { } } & { - 2 } \\ { 2 } & { { } } & { - 1 } & { { } } & { 4 } \\ { - 1 } & { { } } & { 0 } & { { } } & { - 3 } \end{array}$ | \|, by expanding by minors along the first row. |

To evaluate a **3 × 3** determinant we can expand by minors using any row or column. Choosing a row or column other than the first row sometimes makes the work easier.

When we expand by any row or column, we must be careful about the sign of the terms in the expansion. To determine the sign of the terms, we use the following sign pattern chart.

$$
\begin{array} { r l r l } { { 1 } } & { { } + } & { { } - } & { { } + } \\ { { 1 } } & { { } - } & { { } + } & { { } - } \\ { { 1 } } & { { } + } & { { } - } & { { } + } \end{array}
$$

## Sign Pattern

When expanding by minors using a row or column, the sign of the terms in the expansion follow the following pattern.

$$
\begin{array} { r l r l } { { 1 } } & { { } + } & { { } - } & { { } + } \\ { { 1 } } & { { } - } & { { } + } & { { } - } \\ { { 1 } } & { { } + } & { { } - } & { { } + } \end{array}
$$

Notice that the sign pattern in the first row matches the signs between the terms in the expansion by the first row.

![](images/page_7_image_5.jpg)

Since we can expand by any row or column, how do we decide which row or column to use? Usually we try to pick a row or column that will make our calculation easier. If the determinant contains a $0 ,$ using the row or column that contains the 0 will make the calculations easier.

## Example 4.48

## Solution

To expand by minors, we look for a row or column that will make our calculations easier. Since 0 is in the second row and second column, expanding by either of those is a good choice. Since the second row has fewer negatives than the second column, we will expand by the second row.

|  |  |
| --- | --- |
| Expand using the second row. |  |
| Be careful of the signs. |  |
| Evaluate each determinant. |  |
| Simplify. |  |
| Simplify. |  |
| Add. |  |

## Try It 4.95

2 −1 −3 Evaluate the determinant | 0 3 −4 | by expanding by minors. 3 −4 **−3**

## Try It 4.96

−2 −1 −3 Evaluate the determinant | **−1** 2 2 | by expanding by minors. 4 **−4** 0

## Use Cramer’s Rule to Solve Systems of Equations

Cramer’s Rule is a method of solving systems of equations using determinants. It can be derived by solving the general form of the systems of equations by elimination. Here we will demonstrate the rule for both systems of two equations with two variables and fo systems of three equations with three variables.

Let’s start with the systems of two equations with two variables.

## Cramer’s Rule for Solving a System of Two Equations

For the system of equations $\{ \begin{aligned} a_{1}x + b_{1}y &= k_{1} \\ a_{2}x + b_{2}y &= k_{2} \end{aligned}$ , the solution $( x , y )$ can be determined by

$x = \frac{D_{_x}}{D}$ and $y = \frac{D_y}{D_x}$

where $D = \begin{vmatrix} a_{_1} & b_{_1} \\ a_{_2} & b_{_2} \end{vmatrix}$ use the coefficients of the variables.

$D_{x}=\begin{vmatrix}k_{1}&b_{1}\\k_{2}&b_{2}\end{vmatrix}$ replace the x coefficients with the constants.

$D_{y}=\begin{vmatrix}a_{_1}&k_{_1}\\a_{_2}&k_{_2}\end{vmatrix}$ replace the y coefficients with the constants.

Notice that to form the determinant $D ,$ we use take the coefficients of the variables.

$$
a_{1}x + b_{1}y = k_{1}
$$

$$
a_{2}x + b_{2}y = k_{2}
$$

$$
D = \begin{vmatrix} a_{_1} & b_{_1} \\ a_{_2} & b_{_2} \end{vmatrix}
$$

Coefficients

Coefficient

Coefficient

of x

ofy

Notice that to form the determinant $D _ { x }$ and $D _ { y }$ , we substitute the constants for the coefficients of the variable we are finding.

$$
a_{1}x + b_{1}y = k_{1}
$$

$$
a_{2}x + b_{2}y = k_{2}
$$

$$
D_{x}=\begin{vmatrix}k_{1}&b_{1}\\k_{2}&b_{2}\end{vmatrix}
$$

$$
a _ { 1 } x + b _ { 1 } y = k
$$

$$
a_{2}x + b_{2}y = k_{2}
$$

$$
D_{y}=\begin{vmatrix}a_{1}&k_{1}\\a_{2}&k_{2}\end{vmatrix}
$$

Constants Coefficients ofy

Coefficients Constants of x

## Example 4.49

How to Solve a System of Equations Using Cramer’s Rule

<table><tr><td colspan=4> $\{ \begin{array} { c } { { 2 x + y = - 4 } } \\ { { 3 x - 2 y = - 6 } } \end{array}$ Solve using Cramer&#x27;s Rule:</td></tr><tr><td colspan=4>Solution</td></tr><tr><td rowspan=6></td><td>Step 1. Evaluate the determinant $D_{r}$ using the coefficients of thevariables.</td><td></td><td> $\begin{cases}2x + y = -4, \\3x - 2y = -6.\end{cases}$  $D = \begin{vmatrix} 2 & 1 \\ 3 & - 2 \end{vmatrix}$  $\begin{array}{l}D = - 4 - 3, \\D = - 7\end{array}$ </td></tr><tr><td>Step 2. Evaluate the determinant $D_{x}$ Use the constants in place of the xcoefficients.</td><td>We replace the coefficients of $X _ { r }$ 2 and 3, with the constants, $^ { - 4 }$ and-6.</td><td> $D_{x}=\begin{vmatrix}-4&1\\-6&-2\end{vmatrix}$  $\begin{array}{l}D_{x}=8-(-6) \\ D_{x}=14\end{array}$ </td></tr><tr><td>Step 3. Evaluate the determinant $D_{y^{*}}$ Use the constants in place of the ycoefficients.</td><td>We replace the coefficients of $y ;$ 1 and 2, with the constants, $^ { - 4 }$ and-6.</td><td> $D_{y}=\begin{vmatrix}2&-4\\3&-6\end{vmatrix}$  $\begin{array} { l } D _ { y } = - 1 2 - ( - 1 2 ) , \\ D _ { y } = 0 \end{array}$ </td></tr><tr><td>Step 4. Find x and y.</td><td>Substitute in the values of D, Dxand $D_{y}$ </td><td> $X = \frac{D_x}{D} \quad  and   y = \frac{D_y}{D}$  $x = \frac{14}{-7} \;  and  \; y = \frac{0}{-7}$  $x = -2 \quad  and   y = 0$ </td></tr><tr><td>Step 5. Write the solution as anordered pair.</td><td>The ordered pair is $(x,y).$ </td><td>(-2, 0)11</td></tr><tr><td>Step 6. Check that the ordered pairis a solution to both originalequations.</td><td>Substitute x = -2, y = 0 intoboth equations and makesure they are both true.</td><td>(-2, 0) is the solutionto the system.</td></tr></table>

## Try It 4.97

3𝑥 + 𝑦 = −3 Solve using Cramer’s rule: {$2x + 3y = 6$

## Try It 4.98

$- x + y = 2$ Solve using Cramer’s rule: { $2 x + y = - 4$

## How To

## Solve a system of two equations using Cramer’s rule.

1. Step 1. Evaluate the determinant D, using the coefficients of the variables.

2. Step 2. Evaluate the determinant $D _ { x }$ . Use the constants in place of the x coefficients.

3. Step 3. Evaluate the determinant $D _ { y }$ . Use the constants in place of the y coefficients.

4. Step 4. Find x and $\begin{array} { r } { y .   x = \frac { D _ { x } } { D } ,   y = \frac { D _ { y } ^ { ' } } { D } } \end{array}$

5. Step 5. Write the solution as an ordered pair.

6. Step 6. Check that the ordered pair is a solution to both original equations.

To solve a system of three equations with three variables with Cramer’s Rule, we basically do what we did for a system of two equations. However, we now have to solve for three variables to get the solution. The determinants are also going to be $3 \times 3$ which will make our work more interesting!

## Cramer’s Rule for Solving a System of Three Equations

$$
a_{1}x + b_{1}y + c_{1}z = k_{1}
$$

For the system of equations $\{ a_{2}x + b_{2}y + c_{2}z = k_{2}$ , the solution $( x , y , z )$ can be determined by $a_{3}x + b_{3}y + c_{3}z = k_{3}$

$$
x = \frac{D_x}{D}, y = \frac{D_y}{D}  and  z = \frac{D_z}{D}
$$

where $D = \begin{vmatrix} a_{_1} & b_{_1} & c_{_1} \\ a_{_2} & b_{_2} & c_{_2} \\ a_{_3} & b_{_3} & c_{_3} \end{vmatrix}$ use the coefficients of the variables.

$D_{x}=\begin{vmatrix}k_{_1} & b_{_1} & c_{_1} \\k_{_2} & b_{_2} & c_{_2} \\k_{_3} & b_{_3} & c_{_3}\end{vmatrix}$ replace the x coefficients with the constants.

$D_{y}=\begin{vmatrix}a_{1}&k_{1}&c_{1}\\a_{2}&k_{2}&c_{2}\\a_{3}&k_{3}&c_{3}\end{vmatrix}$ replace the y coefficients with the constants.

$D_{z}=\begin{vmatrix}a_{_1}b_{_1}k_{_1}\\a_{_2}b_{_2}k_{_2}\\a_{_3}b_{_3}k_{_3}\end{vmatrix}$ replace the z coefficients with the constants.

$$
3x - 5y + 4z = 5
$$

Solve the system of equations using Cramer’s Rule: { $5x + 2y + z = 0$

$$
2x + 3y - 2z = 3
$$

## Solution

| Evaluate the determinant D. |  |
| --- | --- |
| Expand by minors using column 1. |  |
|  |  |
| Evaluate the determinants. |  |
| Simplify. |  |
| Simplify. |  |
| Simplify. |  |
| Evaluate the determinant𝐷<sub>𝑥</sub> . Use the constants to replace the coefficients of x. |  |
| Expand by minors using column 1. |  |
| Evaluate the determinants. |  |
| Simplify. |  |
| Simplify. |  |
| Evaluate the determinant𝐷<sub>𝑦</sub> . Use the constants to replace the coefficients ofy. |  |
|  |  |
| Evaluate the determinants. |  |
| Simplify. |  |
| Simplify. |  |
| Simplify. |  |
| Evaluate the determinant𝐷<sub>𝑧</sub> . Use the constants to replace the coefficients of z. |  |
|  |  |
| Evaluate the determinants. |  |
| Simplify. |  |
| Simplify. |  |
| Simplify. |  |
| Find x,y, and z. |  |
| Substitute in the values. |  |
| Simplify. |  |
| Write the solution as an ordered triple. |  |
| Check that the ordered triple is a solution to all three original equations. | We leave the check to you. |
|  | The solution is (2,-3,-4). |

## Try It 4.99

$$
3x + 8y + 2z = -5
$$

Solve the system of equations using Cramer’s Rule: { **2𝑥 + 5𝑦 − 3𝑧 = 0**

$$
x + 2y - 2z = -1
$$

**Try It 4.100**

$$
3x + y - 6z = -3
$$

Solve the system of equations using Cramer’s Rule: { $2x + 6y + 3z = 0$

$$
3x + 2y - 3z = -6
$$

Cramer’s rule does not work when the value of the D determinant is 0, as this would mean we would be dividing by 0. But when $D = 0 ,$ **,** the system is either inconsistent or dependent.

When the value of $D = 0$ and $D _ { x } , D _ { y }$ and $D _ { z }$ are all zero, the system is consistent and dependent and there are infinitely many solutions.

When the value of $D = 0$ and $D _ { x }   , D _ { y }$ and $D _ { z }$ are not all zero, the system is inconsistent and there is no solution.

## Dependent and Inconsistent Systems of Equations

For any system of equations, where the value of the determinant $D = 0 ,$

**𝐕𝐚𝐥𝐮𝐞 𝐨𝐟 𝐝𝐞𝐭𝐞𝐫𝐦𝐢𝐧𝐚𝐧𝐭𝐬**

$D = 0$ and $D _ { x }   , D _ { y }$ **and** $D _ { z }$ **are all zero**

**𝐓𝐲𝐩𝐞 𝐨𝐟 𝐬𝐲𝐬𝐭𝐞𝐦**

$D = 0$ and $D _ { x }   , D _ { y }$ **and** $D _ { z }$ **are not all zero**

**consistent and dependent**

**inconsistent**

**𝐒𝐨𝐥𝐮𝐭𝐢𝐨𝐧 infinitely many solutions no solution**

In the next example, we will use the values of the determinants to find the solution of the system.

## Example 4.51

Solve the system of equations using Cramer’s rule : $\left\{ \begin{array}{c} x + 3y = 4 \\ - 2x - 6y = 3 \end{array} \right.$

## Solution

**Evaluate the determinant** $D ,$ **using the coefficients of the variables.**

$$
\{ \begin{array}{c} x + 3y = 4 \\ - 2x - 6y = 3 \end{array}
$$

$$
D = | \begin{array} { c c c } { { 1 } } & { { } } & { { 3 } } \\ { { - 2 } } & { { } } & { { - 6 } } \end{array} |
$$

$$
D = - 6 - ( - 6 )
$$

$$
D = 0
$$

We cannot use Cramer’s Rule to solve this system. But by looking at the value of the determinants $D _ { x }$ and $D _ { y }$ , we can determine whether the system is dependent or inconsistent.

Evaluate the determinant $D _ { x }$

$$
D _ { x } = | \begin{array} { c c c } { { 4 } } & { { } } & { { 3 } } \\ { { 3 } } & { { } } & { { - 6 } } \end{array} |
$$

$$
D _ { x } = - 2 4 - 9
$$

$$
D _ { x } = 1 5
$$

Since all the determinants are not zero, the system is inconsistent. There is no solution.

## Try It 4.101

Solve the system of equations using Cramer’s rule: {

$$
4 x - 3 y = 8
$$

$$
8x - 6y = 14
$$

**Try It 4.102**

Solve the system of equations using Cramer’s rule: $\{ \begin{array} { r } { x = - 3 y + 4 , } \\ { 2 x + 6 y = 8 . } \end{array}$

## Solve Applications using Determinants

An interesting application of determinants allows us to test if points are collinear. Three points $( x _ { 1 }   , y _ { _ 1 } ) ,   ( x _ { 2 }   , y _ { _ 2 } )$ and $( x _ { 3 } , y _ { _ 3 } )$ are collinear if and only if the determinant below is zero.

$$
\begin{array} { r l r } { \vert x _ { 1 } } & { { } y _ { 1 } } & { 1 } \\ { \vert x _ { 2 } } & { { } y _ { 2 } } & { 1 } \\ { x _ { 3 } } & { { } y _ { 3 } } & { 1 } \end{array} \vert = 0 .
$$

## Test for Collinear Points

Three points $( x _ { 1 }   , y _ { _ 1 } ) ,   ( x _ { 2 }   , y _ { _ 2 } )$ and $( x _ { 3 }   , y _ { _ 3 } )$ are collinear if and only if

$$
\begin{array} { r l r } { \vert x _ { 1 } } & { { } y _ { 1 } } & { 1 } \\ { \vert x _ { 2 } } & { { } y _ { 2 } } & { 1 } \\ { x _ { 3 } } & { { } y _ { 3 } } & { 1 } \end{array} \vert = 0 .
$$

We will use this property in the next example.

## Example 4.52

Determine whether the points **(5, −5), (4, −3),** and **(3, −1)** are collinear.

## Solution

|  |  |
| --- | --- |
| Substitute the values into the determinant. (5,-5), (4,-3), and (3,-1) |  |
| Evaluate the determinant by expanding by minors using column 3. |  |
| Evaluate the determinants. |  |
| Simplify. |  |
| Simplify. |  |
|  | The value of the determinate is 0, so the points are collinear. |

## Try It 4.103

Determine whether the points **(3, −2), (5, −3),** and **(1, −1)** are collinear.

## Try It 4.104

Determine whether the points **(−4, −1), (−6, 2),** and **(−2, −4)** are collinear.

## Media

Access these online resources for additional instruction and practice with solving systems of linear inequalities by graphing.

Solving Systems of Linear Inequalities by Graphing

Systems of Linear Inequalities

## Section 4.6 Exercises

## Practice Makes Perfect

**Evaluate the Determinant of a 2 × 2 Matrix**

In the following exercises, evaluate the determinate of each square matrix.

$$
\begin{array} { r l } { { 2 3 2 . } } \\ { { [ \begin{array} { l l } { 6 } & { - 2 } \\ { 3 } & { - 1 } \end{array} ] } } \end{array}
$$

$$
\begin{array} { r l } & { 2 3 3 . } \\ & { [ \begin{array} { l l } { - 4 } & { 8 } \\ { - 3 } & { 5 } \end{array} ] } \end{array}
$$

$$
\begin{array} { r l } { { 2 3 4 . } } \\ { { [ \begin{array} { l l } { - 3 } & { \quad 5 } \\ { 0 } & { \quad - 4 } \end{array} ] } } \end{array}
$$

$$
\begin{array} { r l } { { 2 3 5 . } } \\ { { [ \begin{array} { l l } { - 2 } & { \quad 0 } \\ { 7 } & { \quad - 5 } \end{array} ] } } \end{array}
$$

## Evaluate the Determinant of a 3 × 3 Matrix

In the following exercises, find and then evaluate the indicated minors.

```txt
236.
3 −1 4
| −1 0 −2 |
−4 1 5
Find the minor ⓐ 𝑎1 ⓑ 𝑏2 ⓒ 𝑐3
```

$$
\begin{array}{l}237. \\\quad -1 \quad -3 \quad 2 \\\quad 4 \quad -2 \quad -1 \\\quad -2 \quad 0 \quad -3 \\ Find the minor  \textcircled{a}   a_1 \textcircled{b}   b_1 \textcircled{c}   c_2.\end{array}
$$

$$
\begin{array}{l}238. \\\begin{array}{cccc}2 & -3 & -4 \\-1 & 2 & -3 \\0 & -1 & -2 \\\end{array}| \\ Find the minor  \textcircled{a}   a_2 \textcircled{b}   b_2 \textcircled{c}   c_2.\end{array}
$$

$$
\begin{array}{l}239. \\\quad -2 \quad -2 \quad 3 \\\quad \mid \begin{array}{ccc}1 & -3 & 0 \\-2 & 3 & -2 \\\end{array} \mid \\ Find the minor  \textcircled{a}   a_3 \textcircled{b}   b_3 \textcircled{c}   c_3.\end{array}
$$

In the following exercises, evaluate each determinant by expanding by minors along the first row.

$$
\begin{array}{ccc}240. &  &  \\-2 & 3 & -1 \\\mid -1 & 2 & -2 \\3 & 1 & -3 \\\end{array}\mid
$$

$$
\begin{array} { r l r l r l } { { 2 } 4 1 . } & { { } } & { } & { } & { } & { } \\ { 4 } & { { } } & { - 1 } & { { } } & { - 2 } \\ { \mid - 3 } & { { } } & { - 2 } & { { } } & { \mid } \\ { - 2 } & { { } } & { - 5 } & { { } } & { 7 } \end{array} \mid
$$

$$
\begin{array}{ccc}242. &  \\-2 & -3 & -4 \\\mid 5 & -6 & 7 \mid \\-1 & 2 & 0 \\\end{array}
$$

$$
\begin{array} { r l r l r l } { { 2 } 4 3 . } & { { } } & { } & { } & { } & { } \\ { 1 } & { { } } & { 3 } & { { } } & { - 2 } \\ { 1 } & { { } 5 } & { { } } & { - 6 } & { { } } & { 4 } \\ { 0 } & { { } } & { - 2 } & { { } } & { - 1 } \end{array} \vert .
$$

In the following exercises, evaluate each determinant by expanding by minors.

$$
\begin{array}{ccc}244. &  \\-5 & -1 & -4 \\\mid 4 & 0 & -3 \\2 & -2 & 6 \\\end{array}
$$

$$
\begin{array} { r l r l r l } { { 2 } 4 5 . } & { { } } & { } & { } & { } & { } \\ { 4 } & { { } } & { - 1 } & { { } } & { 3 } \\ { 1 } & { { } 3 } & { { } } & { - 2 } & { { } } & { 2 } \\ { - 1 } & { { } } & { 0 } & { { } } & { 4 } \end{array} \vert
$$

$$
\begin{array} { r l r l r l } { { 2 } 4 6 . } & { { } } & { } & { } & { } & { } \\ { 3 } & { { } } & { 5 } & { { } } & { 4 } \\ { 1 \quad - 1 } & { { } } & { 3 } & { { } } & { 0 } \\ { - 2 } & { { } } & { 6 } & { { } } & { 1 } \end{array} \vert
$$

$$
\begin{array} { r l r l r l } { { 2 } 4 7 . } & { { } } & { } & { } & { } & { } \\ { 2 } & { { } } & { - 4 } & { { } } & { - 3 } \\ { 1 } & { { } 5 } & { { } } & { - 1 } & { { } } & { - 4 } \\ { 3 } & { { } } & { 2 } & { { } } & { 0 } \end{array} \vert
$$

## Use Cramer’s Rule to Solve Systems of Equations

In the following exercises, solve each system of equations using Cramer’s Rule.

$$
\begin{array}{l}248. \\\left\{ \begin{array}{l}- 2x + 3y = 3 \\x + 3y = 12\end{array} \right.\end{array}
$$

$$
\begin{aligned}&249. \\&\{ \begin{array}{l}x - 2y = - 5 \\2x - 3y = - 4\end{array} \}\end{aligned}
$$

$$
\begin{aligned}&250. \\&\left\{ \begin{array}{l}x - 3y = - 9, \\2x + 5y = 4.\end{array} \right.\end{aligned}
$$

$$
\begin{array}{l}251. \\\left\{ \begin{array}{l} \ 2x + y = - 4 \\ 3x - 2y = - 6 \end{array} \right.\end{array}
$$

$$
\{ \begin{array}{l} x - 2y = - 5 \\ 2x - 3y = - 4 \end{array}
$$

$$
\begin{aligned}&253. \\&\left\{ \begin{array}{l}x - 3y = - 9, \\2x + 5y = 4.\end{array} \right.\end{aligned}
$$

$$
\begin{array}{l}254. \\ \left\{ \begin{array}{c} 5x - 3y = - 1 \\ 2x - y = 2 \end{array} \right.\end{array}
$$

$$
\begin{aligned}&255. \\&\{ \begin{array}{l}3x + 8y = - 3, \\2x + 5y = - 3.\end{array}\end{aligned}
$$

$$
\begin{aligned}&256. \\&\left\{\begin{array}{l}6x - 5y + 2z = 3 \\2x + y - 4z = 5 \\3x - 3y + z = -1\end{array}\right.\end{aligned}
$$

$$
\begin{aligned}&257. \\&\left\{\begin{array}{l}\quad 4x - 3y + z = 7 \\2x - 5y - 4z = 3 \\3x - 2y - 2z = -7\end{array}\right.\end{aligned}
$$

$$
2x - 5y + 3z = 8
$$

$$
3x - y + 4z = 7
$$

$$
x + 3y + 2z = -3
$$

$$
\begin{aligned}&259. \\&\left\{\begin{array}{l}11x + 9y + 2z = - 9 \\7x + 5y + 3z = - 7 \\4x + 3y + z = - 3 \\\end{array}\right.\end{aligned}
$$

$$
\begin{array}{l}260. \\ \quad x + 2z = 0 \\ \{ \begin{array}{c} 4y + 3z = - 2 \\ 2x - 5y = 3 \end{array} \}\end{array}
$$

$$
\begin{aligned}&261. \\&\quad \begin{aligned}&2x + 5y = 4 \\&3y - z = 3 \\&4x + 3z = -3\end{aligned}\end{aligned}
$$

$$
\begin{aligned}262. \\2y + 3z &= -1 \\\{ 5x + 3y &= -6 \\7x + z &= 1\end{aligned}
$$

$$
\begin{array}{l}263. \\\quad 3x - z = - 3 \\\{ \begin{array}{l}5y + 2z = - 6 \\4x + 3y = - 8 \\\end{array} \\\end{array}
$$

$$
\begin{array}{l}264.\\\left\{\begin{array}{l}\quad 2x + y = 3 \\6x + 3y = 9\end{array}\right.\end{array}
$$

$$
\begin{aligned}&265. \\&\left\{ \begin{array}{c}x - 4y = - 1 \\- 3x + 12y = 3 \\\end{array} \right.\end{aligned}
$$

$$
\begin{aligned}&266. \\&\{ \begin{array}{l} \ - 3x - y = 4 \\ 6x + 2y = - 16 \end{array} \}\end{aligned}
$$

```txt
267.
4𝑥 + 3𝑦 = 2
{
20𝑥 + 15𝑦 = 5
```

```txt
268.
𝑥 + 𝑦 − 3𝑧 = −1
{ 𝑦 − 𝑧 = 0
−𝑥 + 2𝑦 = 1
```

$$
\begin{aligned}&269. \\&\left\{\begin{array}{c}2x + 3y + z = 12 \\x + y + z = 9 \\3x + 4y + 2z = 20\end{array}\right.\end{aligned}
$$

$$
\begin{aligned}&270. \\&\quad 3x + 4y - 3z = - 20 \\&\quad \{ \begin{array}{r} 2x + 3y - z = - 12 \\ x + y - 2z = 6 \end{array} \}\end{aligned}
$$

$$
\begin{aligned}&271. \\&\left\{\begin{array}{l}\quad x - 2y + 3z = 1 \\\quad x + y - 3z = 7 \\3x - 4y + 5z = 7\end{array}\right.\end{aligned}
$$

## Solve Applications Using Determinants

In the following exercises, determine whether the given points are collinear.

272.

**(0, 1), (2, 0),** and **(−2, 2) .**

273.

**(0, −5), (−2, −2),** and **(2, −8) .**

274.

**(4, −3), (6, −4),** and **(2, −2) .**

275.

**(−2, 1), (−4, 4),** and **(0, −2) .**

## Writing Exercises

276.

Explain the difference between a square matrix and its determinant. Give an example of each.

277.

Explain what is meant by the minor of an entry in a square matrix.

278.

Explain how to decide which row or column you will use to expand a **3 × 3** determinant.

279.

Explain the steps for solving a system of equations using Cramer’s rule.

## Self Check

ⓐ After completing the exercises, use this checklist to evaluate your mastery of the objectives of this section.

| I can... | Confidently | With somehelp | No-I don'tget it! |
| --- | --- | --- | --- |
| evaluate the determinant of a 2 × 2 matrix. |  |  |  |
| evaluate the determinant of a 3 × 3 matrix. |  |  |  |
| use Cramer's rule to solve systems of equations. |  |  |  |
| solve applications using determinants. |  |  |  |

ⓑ After reviewing this checklist, what will you do to become confident for all objectives?

“4.6 Solve Systems of Equations Using Determinants” from Intermediate Algebra by Lynn Marecek, OpenStax. Licensed under Creative Commons Attribution License (CC BY 4.0). [http://creativecommons.org/licenses/by/4.0/](http://creativecommons.org/licenses/by/4.0/)Original page: [https://openstax.org/books/intermediate-algebra/pages/4-6-solve-systems-of-equations-using-determinants](https://openstax.org/books/intermediate-algebra/pages/4-6-solve-systems-of-equations-using-determinants)Access for free at openstax.org. Changes: reformatted as a printable page for evaluating ReadableSTEM; image alt text left out of this print; no wording edited. ReadableSTEM is not affiliated with or endorsed by OpenStax.